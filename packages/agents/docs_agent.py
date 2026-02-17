"""Docs Agent — scans Google Docs and comments for strategy-relevant signals."""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from packages.store.db import RecapDB
from packages.store.schema import (
    CommentSnapshot,
    DocFile,
    DocSnapshot,
    Evidence,
    Signal,
)

log = logging.getLogger(__name__)

# Keywords that bump severity
HIGH_SIGNAL_KEYWORDS = [
    "pricing change",
    "contract renegotiation",
    "churn risk",
    "escalation",
    "p0",
    "p1",
    "blocker",
    "deadline",
    "urgent",
]


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _is_recent(iso_timestamp: str, lookback_hours: int) -> bool:
    """Check whether a timestamp falls within the lookback window."""
    try:
        # Handle both Z-suffix and +00:00 formats
        ts = iso_timestamp.replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
        return dt >= cutoff
    except (ValueError, TypeError):
        return False


def _keyword_severity(text: str) -> int:
    """Return an elevated severity (3-5) if high-signal keywords appear."""
    lower = text.lower()
    hits = sum(1 for kw in HIGH_SIGNAL_KEYWORDS if kw in lower)
    if hits >= 3:
        return 5
    if hits >= 2:
        return 4
    if hits >= 1:
        return 3
    return 0


def run(
    docs: list[dict[str, Any]],
    comments_by_file: dict[str, list[dict[str, Any]]],
    doc_texts: dict[str, str],
    db: RecapDB,
    lookback_hours: int = 24,
) -> list[Signal]:
    """Process fetched docs and comments, return a list of Signals.

    Args:
        docs: output from google_drive.list_docs_in_folders
        comments_by_file: {file_id: [comment_dict, ...]}
        doc_texts: {file_id: plain_text}
        db: database handle
        lookback_hours: how far back to consider changes
    """
    now_iso = datetime.utcnow().isoformat() + "Z"
    signals: list[Signal] = []

    for doc_info in docs:
        fid = doc_info["file_id"]
        name = doc_info["name"]
        link = doc_info["webViewLink"]
        modified = doc_info.get("modifiedTime", "")

        # ── Persist doc file record ───────────────────────────────────
        db.upsert_doc_file(
            DocFile(
                file_id=fid,
                name=name,
                mime_type=doc_info["mime_type"],
                web_view_link=link,
            )
        )

        # ── Detect content change ─────────────────────────────────────
        text = doc_texts.get(fid, "")
        chash = _content_hash(text)

        # Fetch previous snapshot BEFORE saving the new one
        prev = db.get_latest_doc_snapshot(fid)
        content_changed = prev is not None and prev.content_hash != chash

        # Now persist the current snapshot
        db.save_doc_snapshot(
            DocSnapshot(
                file_id=fid,
                snapshot_at=now_iso,
                content_hash=chash,
                content_text=text,
            )
        )
        recently_modified = _is_recent(modified, lookback_hours)

        if recently_modified and content_changed:
            severity = max(2, _keyword_severity(text))
            signals.append(
                Signal(
                    source="docs",
                    type="update",
                    severity=severity,
                    confidence=0.8,
                    title=f"Doc updated: {name}",
                    summary=f'"{name}" was modified in the last {lookback_hours}h. '
                    "Content has changed since the previous snapshot.",
                    time_sensitivity="today",
                    evidence=[
                        Evidence(
                            kind="gdoc",
                            title=name,
                            url=link,
                            snippet=text[:300] if text else None,
                            timestamp=modified,
                        )
                    ],
                    recommended_next_steps=[
                        f"Review latest changes in: {link}",
                    ],
                    key_questions=[
                        f"What changed in '{name}' and does it affect current priorities?",
                    ],
                    tags=["doc-change"],
                )
            )
        elif recently_modified:
            # First time seeing this doc, or hash matches — still note it
            kw_sev = _keyword_severity(text)
            if kw_sev > 0:
                signals.append(
                    Signal(
                        source="docs",
                        type="risk" if kw_sev >= 4 else "update",
                        severity=kw_sev,
                        confidence=0.6,
                        title=f"High-signal keywords in: {name}",
                        summary=f'"{name}" contains keywords flagged for attention.',
                        time_sensitivity="today" if kw_sev >= 4 else "this_week",
                        evidence=[
                            Evidence(
                                kind="gdoc",
                                title=name,
                                url=link,
                                snippet=text[:300] if text else None,
                                timestamp=modified,
                            )
                        ],
                        tags=["keywords"],
                    )
                )

        # ── Process comments ──────────────────────────────────────────
        file_comments = comments_by_file.get(fid, [])
        for c in file_comments:
            # Persist snapshot
            db.save_comment_snapshot(
                CommentSnapshot(
                    file_id=fid,
                    comment_id=c["comment_id"],
                    snapshot_at=now_iso,
                    status=c["status"],
                    author=c["author"],
                    updated_at=c.get("updatedTime", ""),
                    content_text=c.get("content", ""),
                    quoted_file_content=c.get("quotedFileContent"),
                    web_view_link=c.get("webViewLink", link),
                )
            )

            # Only surface unresolved comments updated recently
            if c["status"] != "open":
                continue
            if not _is_recent(c.get("updatedTime", ""), lookback_hours):
                continue

            comment_text = c.get("content", "")
            comment_link = c.get("webViewLink", link)
            severity = max(2, _keyword_severity(comment_text))

            # Determine signal type from content
            lower_text = comment_text.lower()
            if "?" in comment_text:
                sig_type = "question"
            elif any(kw in lower_text for kw in ("risk", "blocker", "urgent")):
                sig_type = "risk"
            elif any(kw in lower_text for kw in ("decision", "approve", "sign off")):
                sig_type = "decision"
            else:
                sig_type = "update"

            signals.append(
                Signal(
                    source="docs",
                    type=sig_type,
                    severity=severity,
                    confidence=0.7,
                    title=f"Comment by {c['author']} on '{name}'",
                    summary=comment_text[:500],
                    owner=c["author"],
                    time_sensitivity="today",
                    evidence=[
                        Evidence(
                            kind="comment",
                            title=f"Comment on {name}",
                            url=comment_link,
                            snippet=comment_text[:200],
                            timestamp=c.get("updatedTime"),
                        )
                    ],
                    key_questions=(
                        [comment_text[:300]] if sig_type == "question" else []
                    ),
                    tags=["comment", "unresolved"],
                )
            )

    log.info("Docs agent produced %d signals", len(signals))
    return signals
