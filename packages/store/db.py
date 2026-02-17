"""SQLite storage layer for the Daily Strategy Recap system."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime

from packages.store.schema import (
    CommentSnapshot,
    DocFile,
    DocSnapshot,
    MetricSnapshot,
    RunRecord,
    Signal,
)

log = logging.getLogger(__name__)

DDL = """
CREATE TABLE IF NOT EXISTS runs (
    run_id       TEXT PRIMARY KEY,
    started_at   TEXT NOT NULL,
    ended_at     TEXT,
    status       TEXT NOT NULL DEFAULT 'running',
    stats_json   TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS doc_files (
    file_id        TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    mime_type      TEXT NOT NULL,
    web_view_link  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS doc_snapshots (
    file_id       TEXT NOT NULL,
    snapshot_at   TEXT NOT NULL,
    content_hash  TEXT NOT NULL,
    content_text  TEXT NOT NULL,
    PRIMARY KEY (file_id, snapshot_at)
);

CREATE TABLE IF NOT EXISTS comment_snapshots (
    file_id              TEXT NOT NULL,
    comment_id           TEXT NOT NULL,
    snapshot_at          TEXT NOT NULL,
    status               TEXT NOT NULL,
    author               TEXT NOT NULL,
    updated_at           TEXT NOT NULL,
    content_text         TEXT NOT NULL,
    quoted_file_content  TEXT,
    web_view_link        TEXT NOT NULL,
    PRIMARY KEY (file_id, comment_id, snapshot_at)
);

CREATE TABLE IF NOT EXISTS metric_snapshots (
    metric_name   TEXT NOT NULL,
    snapshot_at   TEXT NOT NULL,
    payload_json  TEXT NOT NULL,
    PRIMARY KEY (metric_name, snapshot_at)
);

CREATE TABLE IF NOT EXISTS signals (
    run_id           TEXT NOT NULL,
    signal_id        TEXT PRIMARY KEY,
    source           TEXT NOT NULL,
    type             TEXT NOT NULL,
    severity         INTEGER NOT NULL,
    confidence       REAL NOT NULL,
    title            TEXT NOT NULL,
    summary          TEXT NOT NULL,
    owner            TEXT,
    time_sensitivity TEXT,
    evidence_json    TEXT NOT NULL DEFAULT '[]',
    next_steps_json  TEXT NOT NULL DEFAULT '[]',
    questions_json   TEXT NOT NULL DEFAULT '[]',
    tags_json        TEXT NOT NULL DEFAULT '[]'
);
"""


class RecapDB:
    """Thin wrapper around SQLite for the recap pipeline."""

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or os.getenv("DATABASE_PATH", "data/recap.db")
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.executescript(DDL)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # ── Runs ──────────────────────────────────────────────────────────

    def start_run(self, run: RunRecord) -> None:
        self.conn.execute(
            "INSERT INTO runs (run_id, started_at, status, stats_json) VALUES (?,?,?,?)",
            (run.run_id, run.started_at, run.status, json.dumps(run.stats_json)),
        )
        self.conn.commit()

    def finish_run(self, run_id: str, status: str, stats: dict) -> None:
        now = datetime.utcnow().isoformat() + "Z"
        self.conn.execute(
            "UPDATE runs SET ended_at=?, status=?, stats_json=? WHERE run_id=?",
            (now, status, json.dumps(stats), run_id),
        )
        self.conn.commit()

    # ── Doc files ─────────────────────────────────────────────────────

    def upsert_doc_file(self, doc: DocFile) -> None:
        self.conn.execute(
            """INSERT INTO doc_files (file_id, name, mime_type, web_view_link)
               VALUES (?,?,?,?)
               ON CONFLICT(file_id) DO UPDATE SET
                 name=excluded.name,
                 mime_type=excluded.mime_type,
                 web_view_link=excluded.web_view_link""",
            (doc.file_id, doc.name, doc.mime_type, doc.web_view_link),
        )
        self.conn.commit()

    # ── Doc snapshots ─────────────────────────────────────────────────

    def save_doc_snapshot(self, snap: DocSnapshot) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO doc_snapshots
               (file_id, snapshot_at, content_hash, content_text)
               VALUES (?,?,?,?)""",
            (snap.file_id, snap.snapshot_at, snap.content_hash, snap.content_text),
        )
        self.conn.commit()

    def get_latest_doc_snapshot(self, file_id: str) -> DocSnapshot | None:
        row = self.conn.execute(
            """SELECT file_id, snapshot_at, content_hash, content_text
               FROM doc_snapshots WHERE file_id=?
               ORDER BY snapshot_at DESC LIMIT 1""",
            (file_id,),
        ).fetchone()
        if row is None:
            return None
        return DocSnapshot(
            file_id=row["file_id"],
            snapshot_at=row["snapshot_at"],
            content_hash=row["content_hash"],
            content_text=row["content_text"],
        )

    # ── Comment snapshots ─────────────────────────────────────────────

    def save_comment_snapshot(self, snap: CommentSnapshot) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO comment_snapshots
               (file_id, comment_id, snapshot_at, status, author,
                updated_at, content_text, quoted_file_content, web_view_link)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                snap.file_id,
                snap.comment_id,
                snap.snapshot_at,
                snap.status,
                snap.author,
                snap.updated_at,
                snap.content_text,
                snap.quoted_file_content,
                snap.web_view_link,
            ),
        )
        self.conn.commit()

    # ── Metric snapshots ──────────────────────────────────────────────

    def save_metric_snapshot(self, snap: MetricSnapshot) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO metric_snapshots
               (metric_name, snapshot_at, payload_json)
               VALUES (?,?,?)""",
            (snap.metric_name, snap.snapshot_at, json.dumps(snap.payload_json)),
        )
        self.conn.commit()

    def get_latest_metric_snapshot(self, metric_name: str) -> MetricSnapshot | None:
        row = self.conn.execute(
            """SELECT metric_name, snapshot_at, payload_json
               FROM metric_snapshots WHERE metric_name=?
               ORDER BY snapshot_at DESC LIMIT 1""",
            (metric_name,),
        ).fetchone()
        if row is None:
            return None
        return MetricSnapshot(
            metric_name=row["metric_name"],
            snapshot_at=row["snapshot_at"],
            payload_json=json.loads(row["payload_json"]),
        )

    # ── Signals ───────────────────────────────────────────────────────

    def save_signal(self, run_id: str, signal: Signal) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO signals
               (run_id, signal_id, source, type, severity, confidence,
                title, summary, owner, time_sensitivity,
                evidence_json, next_steps_json, questions_json, tags_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                run_id,
                signal.signal_id,
                signal.source,
                signal.type,
                signal.severity,
                signal.confidence,
                signal.title,
                signal.summary,
                signal.owner,
                signal.time_sensitivity,
                json.dumps([e.to_dict() for e in signal.evidence]),
                json.dumps(signal.recommended_next_steps),
                json.dumps(signal.key_questions),
                json.dumps(signal.tags),
            ),
        )
        self.conn.commit()

    def get_signals_for_run(self, run_id: str) -> list[Signal]:
        rows = self.conn.execute(
            "SELECT * FROM signals WHERE run_id=?", (run_id,)
        ).fetchall()
        signals = []
        for r in rows:
            from packages.store.schema import Evidence

            evidence = [Evidence(**e) for e in json.loads(r["evidence_json"])]
            signals.append(
                Signal(
                    signal_id=r["signal_id"],
                    source=r["source"],
                    type=r["type"],
                    severity=r["severity"],
                    confidence=r["confidence"],
                    title=r["title"],
                    summary=r["summary"],
                    owner=r["owner"],
                    time_sensitivity=r["time_sensitivity"],
                    evidence=evidence,
                    recommended_next_steps=json.loads(r["next_steps_json"]),
                    key_questions=json.loads(r["questions_json"]),
                    tags=json.loads(r["tags_json"]),
                )
            )
        return signals
