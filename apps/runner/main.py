"""Daily Strategy Recap — pipeline runner.

Usage:
    python -m apps.runner.main
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime

import yaml
from dotenv import load_dotenv

# Ensure repo root is on sys.path so package imports work
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from packages.agents import docs_agent, leader_agent, metrics_agent
from packages.connectors import email_sender
from packages.connectors.google_docs import fetch_doc_plain_text
from packages.connectors.google_drive import list_comments, list_docs_in_folders
from packages.connectors.metrics import fetch_metrics_snapshot
from packages.store.db import RecapDB
from packages.store.schema import RunRecord

load_dotenv()

log = logging.getLogger("recap")


def _configure_logging() -> None:
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _load_sources_config(path: str = "configs/sources.yaml") -> dict:
    with open(path, "r") as f:
        return yaml.safe_load(f)


def _load_operating_context(path: str = "context/operating_context.md") -> str:
    with open(path, "r") as f:
        return f.read()


def run_pipeline() -> None:
    """Execute the full daily recap pipeline."""
    _configure_logging()
    log.info("=== Daily Strategy Recap — starting ===")

    db = RecapDB()
    run_record = RunRecord()
    db.start_run(run_record)

    stats: dict = {
        "docs_fetched": 0,
        "comments_fetched": 0,
        "signals_docs": 0,
        "signals_metrics": 0,
        "signals_total": 0,
        "email_sent": False,
    }

    try:
        # ── 1. Load configs ───────────────────────────────────────────
        sources = _load_sources_config()
        operating_context = _load_operating_context()
        folder_ids = sources.get("drive_folder_ids", [])
        mime_types = sources.get("allowed_mime_types", [])
        max_files = sources.get("max_files", 50)
        lookback_hours = sources.get("lookback_hours", 24)

        if not folder_ids or folder_ids == ["REPLACE_WITH_FOLDER_ID_1"]:
            log.warning(
                "No real folder IDs configured in configs/sources.yaml. "
                "Skipping Google Drive fetch."
            )
            docs = []
        else:
            # ── 2. Fetch docs + comments from Google Drive ────────────
            log.info("Fetching docs from %d folders (max %d)", len(folder_ids), max_files)
            docs = list_docs_in_folders(folder_ids, mime_types, max_files)

        stats["docs_fetched"] = len(docs)

        # ── 3. Fetch doc text and comments ────────────────────────────
        doc_texts: dict[str, str] = {}
        comments_by_file: dict[str, list] = {}

        for doc_info in docs:
            fid = doc_info["file_id"]
            try:
                doc_texts[fid] = fetch_doc_plain_text(fid)
            except Exception as exc:
                log.error("Failed to fetch text for %s: %s", fid, exc)
                doc_texts[fid] = ""

            try:
                comments_by_file[fid] = list_comments(fid)
                stats["comments_fetched"] += len(comments_by_file[fid])
            except Exception as exc:
                log.error("Failed to fetch comments for %s: %s", fid, exc)
                comments_by_file[fid] = []

        # ── 4. Run Docs Agent ─────────────────────────────────────────
        log.info("Running docs agent on %d docs", len(docs))
        doc_signals = docs_agent.run(
            docs=docs,
            comments_by_file=comments_by_file,
            doc_texts=doc_texts,
            db=db,
            lookback_hours=lookback_hours,
        )
        stats["signals_docs"] = len(doc_signals)

        # ── 5. Fetch metrics + run Metrics Agent ──────────────────────
        log.info("Fetching metrics snapshot")
        metrics_snapshot = fetch_metrics_snapshot()
        metric_signals = metrics_agent.run(snapshot=metrics_snapshot, db=db)
        stats["signals_metrics"] = len(metric_signals)

        # ── 6. Persist all signals ────────────────────────────────────
        all_signals = doc_signals + metric_signals
        stats["signals_total"] = len(all_signals)
        for sig in all_signals:
            db.save_signal(run_record.run_id, sig)

        # ── 7. Run Leader Agent ───────────────────────────────────────
        log.info("Running leader agent on %d signals", len(all_signals))
        ranked, recap_text = leader_agent.run(
            signals=all_signals,
            operating_context=operating_context,
        )

        stats["recap_length"] = len(recap_text)
        stats["ranked_signals"] = len(ranked)

        # ── 8. Send recap via email ───────────────────────────────────
        if os.getenv("GMAIL_ADDRESS") and os.getenv("GMAIL_APP_PASSWORD"):
            log.info("Sending recap via email")
            ok = email_sender.send_recap(recap_text)
            stats["email_sent"] = ok
            if not ok:
                log.warning("Email send failed — recap was still saved to DB")
        else:
            log.info("GMAIL_ADDRESS not set — printing recap to stdout")
            print("\n" + recap_text + "\n")

        # ── 9. Finalize run ───────────────────────────────────────────
        stats["recap_text"] = recap_text
        db.finish_run(run_record.run_id, "success", stats)
        log.info("=== Run %s completed successfully ===", run_record.run_id)

    except Exception as exc:
        log.exception("Pipeline failed: %s", exc)
        stats["error"] = str(exc)
        db.finish_run(run_record.run_id, "failed", stats)
        raise
    finally:
        db.close()


if __name__ == "__main__":
    run_pipeline()
