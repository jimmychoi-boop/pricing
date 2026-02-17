"""Leader Agent — ranks signals, deduplicates, and produces the Slack recap."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime

import yaml
from rapidfuzz import fuzz

from packages.store.schema import Signal

log = logging.getLogger(__name__)

DEFAULT_THRESHOLDS_CONFIG = "configs/thresholds.yaml"

# Time-sensitivity sort order (lower = more urgent)
TIME_SENSITIVITY_ORDER = {
    "today": 0,
    "this_week": 1,
    "this_month": 2,
    None: 3,
}


def _load_thresholds(config_path: str = DEFAULT_THRESHOLDS_CONFIG) -> dict:
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def _deduplicate(
    signals: list[Signal], similarity_threshold: int = 80
) -> list[Signal]:
    """Remove near-duplicate signals based on title similarity."""
    if not signals:
        return []

    kept: list[Signal] = []
    for sig in signals:
        is_dup = False
        for existing in kept:
            score = fuzz.token_sort_ratio(sig.title, existing.title)
            if score >= similarity_threshold:
                # Keep the one with higher severity
                if sig.severity > existing.severity:
                    kept.remove(existing)
                    kept.append(sig)
                is_dup = True
                break
        if not is_dup:
            kept.append(sig)

    removed = len(signals) - len(kept)
    if removed:
        log.info("Deduplication removed %d signals", removed)
    return kept


def _rank_signals(signals: list[Signal]) -> list[Signal]:
    """Sort signals by severity (desc), time_sensitivity (asc), confidence (desc)."""
    return sorted(
        signals,
        key=lambda s: (
            -s.severity,
            TIME_SENSITIVITY_ORDER.get(s.time_sensitivity, 3),
            -s.confidence,
        ),
    )


def _format_recap(
    ranked: list[Signal],
    operating_context: str,
    top_n: int = 5,
    watchlist_n: int = 3,
) -> str:
    """Build a Slack-ready plain text recap."""
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = []

    lines.append(f":newspaper:  *Daily Strategy Recap* — {now}")
    lines.append("=" * 50)
    lines.append("")

    # ── Top Issues ────────────────────────────────────────────────────
    lines.append(f":rotating_light:  *Top {top_n} Issues*")
    lines.append("")
    top_signals = ranked[:top_n]

    for i, sig in enumerate(top_signals, 1):
        severity_bar = ":red_circle:" if sig.severity >= 4 else ":large_yellow_circle:" if sig.severity >= 3 else ":white_circle:"
        lines.append(f"{i}. {severity_bar} *{sig.title}*  (severity {sig.severity}/5, confidence {sig.confidence:.0%})")
        lines.append(f"   {sig.summary[:200]}")
        # Evidence links
        for ev in sig.evidence[:2]:
            lines.append(f"   :link: <{ev.url}|{ev.title}>")
        if sig.recommended_next_steps:
            lines.append(f"   :arrow_right: {sig.recommended_next_steps[0]}")
        lines.append("")

    # ── Key Questions ─────────────────────────────────────────────────
    questions_by_owner: dict[str, list[str]] = defaultdict(list)
    for sig in ranked:
        owner = sig.owner or "Team"
        for q in sig.key_questions:
            questions_by_owner[owner].append(q)

    if questions_by_owner:
        lines.append(":question:  *Key Questions to Ask Today*")
        lines.append("")
        for owner, qs in questions_by_owner.items():
            lines.append(f"  *{owner}:*")
            for q in qs[:3]:  # cap per owner
                lines.append(f"   • {q}")
            lines.append("")

    # ── Watchlist ─────────────────────────────────────────────────────
    # Items not in top-N but still notable
    watchlist_candidates = ranked[top_n : top_n + watchlist_n + 5]
    watchlist = watchlist_candidates[:watchlist_n]

    if watchlist:
        lines.append(":eyes:  *Watchlist*")
        lines.append("")
        for sig in watchlist:
            lines.append(f"  • *{sig.title}* — {sig.summary[:120]}")
            if sig.evidence:
                lines.append(f"    :link: <{sig.evidence[0].url}|{sig.evidence[0].title}>")
        lines.append("")

    lines.append("_All items are evidence-backed. Links point to source documents or metrics._")
    return "\n".join(lines)


def run(
    signals: list[Signal],
    operating_context: str,
    thresholds_config: str = DEFAULT_THRESHOLDS_CONFIG,
) -> tuple[list[Signal], str]:
    """Deduplicate, rank, and format the final recap.

    Args:
        signals: combined signals from all agents
        operating_context: contents of context/operating_context.md
        thresholds_config: path to thresholds.yaml

    Returns:
        (ranked_signals, recap_text)
    """
    cfg = _load_thresholds(thresholds_config)
    min_severity = cfg.get("min_severity", 2)
    min_confidence = cfg.get("min_confidence", 0.3)
    dedup_sim = cfg.get("dedup_similarity", 80)
    top_n = cfg.get("top_issues", 5)
    watchlist_n = cfg.get("watchlist_items", 3)

    # Filter
    filtered = [
        s
        for s in signals
        if s.severity >= min_severity and s.confidence >= min_confidence
    ]
    log.info(
        "Filtered %d -> %d signals (min_severity=%d, min_confidence=%.1f)",
        len(signals),
        len(filtered),
        min_severity,
        min_confidence,
    )

    # Deduplicate
    deduped = _deduplicate(filtered, similarity_threshold=dedup_sim)

    # Rank
    ranked = _rank_signals(deduped)

    # Format
    recap = _format_recap(
        ranked,
        operating_context,
        top_n=top_n,
        watchlist_n=watchlist_n,
    )

    log.info("Leader agent produced recap with %d ranked signals", len(ranked))
    return ranked, recap
