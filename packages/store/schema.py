"""Shared data models for the Daily Strategy Recap system."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Literal


@dataclass
class Evidence:
    kind: Literal["gdoc", "comment", "metric", "hex", "other"]
    title: str
    url: str
    snippet: str | None = None
    timestamp: str | None = None  # ISO-8601

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Signal:
    source: Literal["docs", "metrics"]
    type: Literal["decision", "risk", "update", "anomaly", "question"]
    severity: int  # 1-5
    confidence: float  # 0.0-1.0
    title: str
    summary: str
    owner: str | None = None
    time_sensitivity: Literal["today", "this_week", "this_month"] | None = None
    evidence: list[Evidence] = field(default_factory=list)
    recommended_next_steps: list[str] = field(default_factory=list)
    key_questions: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    signal_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def to_dict(self) -> dict:
        d = asdict(self)
        d["evidence"] = [e.to_dict() for e in self.evidence]
        return d


@dataclass
class DocFile:
    file_id: str
    name: str
    mime_type: str
    web_view_link: str


@dataclass
class DocSnapshot:
    file_id: str
    snapshot_at: str  # ISO-8601
    content_hash: str
    content_text: str


@dataclass
class CommentSnapshot:
    file_id: str
    comment_id: str
    snapshot_at: str
    status: str  # "open" | "resolved"
    author: str
    updated_at: str
    content_text: str
    quoted_file_content: str | None
    web_view_link: str


@dataclass
class MetricSnapshot:
    metric_name: str
    snapshot_at: str
    payload_json: dict


@dataclass
class RunRecord:
    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    started_at: str = field(
        default_factory=lambda: datetime.utcnow().isoformat() + "Z"
    )
    ended_at: str | None = None
    status: str = "running"
    stats_json: dict = field(default_factory=dict)
