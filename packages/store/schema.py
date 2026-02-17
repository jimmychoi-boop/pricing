"""Shared data models for the Daily Strategy Recap system."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Literal

# ── Segment dimensions used across metrics intelligence ────────────────
SEGMENT_DIMENSIONS = Literal[
    "plan", "sa_ss", "cohort", "region", "channel", "product_line"
]


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


# ── Metrics Intelligence output models ─────────────────────────────────


@dataclass
class MetricDelta:
    """What moved: daily delta for a single metric."""

    metric_name: str
    display_name: str
    current_value: float
    previous_value: float
    abs_delta: float
    pct_change: float
    direction: Literal["up", "down", "flat"]
    unit: str
    is_anomaly: bool
    period: str = "daily"  # daily, weekly, monthly
    timestamp: str | None = None  # ISO-8601

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DriverDecomposition:
    """Why it moved: factor-level attribution for a metric change."""

    metric_name: str
    driver_name: str  # e.g. "expansion", "new_business", "churn", "contraction"
    contribution: float  # absolute contribution to the delta
    pct_of_delta: float  # what % of the total delta this driver explains
    description: str
    evidence: list[Evidence] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["evidence"] = [e.to_dict() for e in self.evidence]
        return d


@dataclass
class SegmentImpact:
    """Who was impacted: segment-level breakdown of a metric movement."""

    metric_name: str
    dimension: str  # plan, sa_ss, cohort, region, channel, product_line
    segment_value: str  # e.g. "Enterprise", "North America", "Q3-2025 cohort"
    segment_delta: float
    segment_pct_change: float
    segment_current: float
    direction: Literal["up", "down", "flat"]
    is_outsized: bool  # True if this segment moved disproportionately
    description: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class FollowUpQuery:
    """Suggested SQL/Hex query to validate or drill into a finding."""

    metric_name: str
    question: str  # human-readable question this query answers
    query_type: Literal["sql", "hex_saved_query", "hex_chart"]
    query_text: str  # SQL or Hex reference ID
    priority: Literal["high", "medium", "low"]
    rationale: str  # why this follow-up matters

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class MetricsIntelligenceOutput:
    """Structured output from the Metrics Intelligence agent (Agent B)."""

    run_timestamp: str
    deltas: list[MetricDelta] = field(default_factory=list)
    anomalies: list[MetricDelta] = field(default_factory=list)
    drivers: list[DriverDecomposition] = field(default_factory=list)
    segments: list[SegmentImpact] = field(default_factory=list)
    follow_up_queries: list[FollowUpQuery] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "run_timestamp": self.run_timestamp,
            "deltas": [d.to_dict() for d in self.deltas],
            "anomalies": [a.to_dict() for a in self.anomalies],
            "drivers": [d.to_dict() for d in self.drivers],
            "segments": [s.to_dict() for s in self.segments],
            "follow_up_queries": [q.to_dict() for q in self.follow_up_queries],
        }


# ── Leader Agent (Agent C) output models ───────────────────────────────


@dataclass
class CrossReference:
    """A connection the Leader found between a doc signal and a metric signal."""

    doc_signal_id: str
    metric_signal_id: str | None  # None if no metric match
    metric_name: str | None
    relationship: Literal["confirms", "contradicts", "adds_context", "unrelated"]
    explanation: str
    combined_severity: int  # re-scored severity after correlation
    combined_evidence: list[Evidence] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["combined_evidence"] = [e.to_dict() for e in self.combined_evidence]
        return d


@dataclass
class Verdict:
    """A synthesized call the Leader makes after cross-referencing all inputs."""

    verdict_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    priority: Literal["critical", "high", "medium", "low"] = "medium"
    title: str = ""
    narrative: str = ""  # the "so what" — plain-language synthesis
    action: str = ""  # the call: what should be done
    owner: str | None = None  # who should act
    deadline: Literal["today", "this_week", "this_month"] | None = None
    supporting_signals: list[str] = field(default_factory=list)  # signal_ids
    cross_references: list[CrossReference] = field(default_factory=list)
    follow_up_queries: list[FollowUpQuery] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["cross_references"] = [c.to_dict() for c in self.cross_references]
        d["follow_up_queries"] = [q.to_dict() for q in self.follow_up_queries]
        return d


@dataclass
class RecapOutput:
    """Full structured output from Agent C (Leader)."""

    run_timestamp: str
    verdicts: list[Verdict] = field(default_factory=list)
    cross_references: list[CrossReference] = field(default_factory=list)
    ranked_signals: list[Signal] = field(default_factory=list)
    recap_text: str = ""

    def to_dict(self) -> dict:
        return {
            "run_timestamp": self.run_timestamp,
            "verdicts": [v.to_dict() for v in self.verdicts],
            "cross_references": [c.to_dict() for c in self.cross_references],
            "ranked_signals": [s.to_dict() for s in self.ranked_signals],
            "recap_text": self.recap_text,
        }


@dataclass
class RunRecord:
    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    started_at: str = field(
        default_factory=lambda: datetime.utcnow().isoformat() + "Z"
    )
    ended_at: str | None = None
    status: str = "running"
    stats_json: dict = field(default_factory=dict)
