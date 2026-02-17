"""Demo script — generates an example Daily Strategy Recap with mock data."""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from packages.store.schema import Evidence, Signal
from packages.agents.leader_agent import _format_recap

# ── Mock signals that simulate a real run ──────────────────────────────

signals = [
    Signal(
        source="docs",
        type="risk",
        severity=5,
        confidence=0.9,
        title="Churn risk flagged in Enterprise deal review",
        summary="Acme Corp contract renewal doc mentions 'churn risk' and 'pricing change' — competitor undercut by 15%. Deal is $420K ARR expiring in 30 days.",
        owner="Sarah K.",
        time_sensitivity="today",
        evidence=[
            Evidence(
                kind="gdoc",
                title="Acme Corp — Q1 Renewal Strategy",
                url="https://docs.google.com/document/d/1abc/edit",
                snippet="Competitor X offered 15% discount. Client requesting pricing match or added services...",
                timestamp="2026-02-17T10:30:00Z",
            )
        ],
        recommended_next_steps=[
            "Schedule executive call with Acme Corp this week",
            "Prepare competitive counter-offer with value-add bundle",
        ],
        key_questions=[
            "Can we match the 15% discount without setting a precedent?",
            "What's the total LTV if we lose Acme Corp?",
        ],
        tags=["churn-risk", "enterprise", "keywords"],
    ),
    Signal(
        source="metrics",
        type="anomaly",
        severity=4,
        confidence=0.85,
        title="Win rate decreased 22.3%",
        summary="Win rate went from 38% to 29.5% (-8.5pp, 22.3% change). Sharpest weekly drop in 6 months.",
        time_sensitivity="today",
        evidence=[
            Evidence(
                kind="metric",
                title="Win Rate snapshot",
                url="metric://win_rate",
                snippet="Previous: 38% (2026-02-10), Current: 29.5% (2026-02-17)",
                timestamp="2026-02-17T08:00:00Z",
            )
        ],
        recommended_next_steps=[
            "Investigate why win rate decreased by 22.3% — check deal stage conversion rates.",
        ],
        key_questions=[
            "Is the win rate drop driven by a specific segment or rep?",
        ],
        tags=["metric-anomaly", "win_rate"],
    ),
    Signal(
        source="docs",
        type="decision",
        severity=4,
        confidence=0.8,
        title="Comment by VP Sales on 'Q1 Pricing Tier Update'",
        summary="Need sign-off by EOD Wednesday on the new mid-market tier. Legal has cleared it but finance wants to see margin impact analysis first.",
        owner="VP Sales",
        time_sensitivity="today",
        evidence=[
            Evidence(
                kind="comment",
                title="Comment on Q1 Pricing Tier Update",
                url="https://docs.google.com/document/d/2def/edit?disco=comment123",
                snippet="Need sign-off by EOD Wednesday on the new mid-market tier...",
                timestamp="2026-02-17T14:15:00Z",
            )
        ],
        key_questions=[
            "Has finance completed the margin impact analysis for the new tier?",
        ],
        tags=["comment", "unresolved", "decision"],
    ),
    Signal(
        source="docs",
        type="update",
        severity=3,
        confidence=0.8,
        title="Doc updated: Competitive Landscape — Feb 2026",
        summary='"Competitive Landscape — Feb 2026" was modified in the last 24h. New section added on Dropbox Sign pricing restructure.',
        time_sensitivity="today",
        evidence=[
            Evidence(
                kind="gdoc",
                title="Competitive Landscape — Feb 2026",
                url="https://docs.google.com/document/d/3ghi/edit",
                snippet="Dropbox Sign announced new tier structure: Essentials at $20/mo, Standard at $30/mo...",
                timestamp="2026-02-17T09:45:00Z",
            )
        ],
        recommended_next_steps=[
            "Review latest changes in: https://docs.google.com/document/d/3ghi/edit",
        ],
        key_questions=[
            "What changed in 'Competitive Landscape' and does it affect current priorities?",
        ],
        tags=["doc-change"],
    ),
    Signal(
        source="metrics",
        type="anomaly",
        severity=3,
        confidence=0.85,
        title="Average deal size increased 18.7%",
        summary="Average deal size went from $32K to $38K (+$6K, 18.7% change). Driven by two large enterprise deals closing this week.",
        time_sensitivity="this_week",
        evidence=[
            Evidence(
                kind="metric",
                title="Avg Deal Size snapshot",
                url="metric://avg_deal_size",
                snippet="Previous: $32K (2026-02-10), Current: $38K (2026-02-17)",
                timestamp="2026-02-17T08:00:00Z",
            )
        ],
        recommended_next_steps=[
            "Verify if deal size increase is sustainable or one-time.",
        ],
        key_questions=[
            "Is the average deal size increase sustainable or pulled up by outliers?",
        ],
        tags=["metric-anomaly", "avg_deal_size"],
    ),
    Signal(
        source="docs",
        type="question",
        severity=3,
        confidence=0.7,
        title="Comment by Product Lead on 'PandaDoc Feature Comparison'",
        summary="Are we still planning to launch the free tier in March? Need to update the comparison matrix if so.",
        owner="Product Lead",
        time_sensitivity="this_week",
        evidence=[
            Evidence(
                kind="comment",
                title="Comment on PandaDoc Feature Comparison",
                url="https://docs.google.com/document/d/4jkl/edit?disco=comment456",
                snippet="Are we still planning to launch the free tier in March?",
                timestamp="2026-02-16T16:30:00Z",
            )
        ],
        key_questions=[
            "Are we still planning to launch the free tier in March?",
        ],
        tags=["comment", "unresolved"],
    ),
    Signal(
        source="docs",
        type="update",
        severity=2,
        confidence=0.6,
        title="Doc updated: Weekly Deal Pipeline Notes",
        summary='"Weekly Deal Pipeline Notes" was modified. Routine weekly update with no high-signal keywords detected.',
        time_sensitivity="this_week",
        evidence=[
            Evidence(
                kind="gdoc",
                title="Weekly Deal Pipeline Notes",
                url="https://docs.google.com/document/d/5mno/edit",
                snippet="Pipeline review: 12 deals in negotiation, 3 expected to close this week...",
                timestamp="2026-02-17T11:00:00Z",
            )
        ],
        tags=["doc-change"],
    ),
    Signal(
        source="metrics",
        type="anomaly",
        severity=2,
        confidence=0.85,
        title="Pipeline coverage increased 16.2%",
        summary="Pipeline coverage went from 3.1x to 3.6x (+0.5x, 16.2% change).",
        time_sensitivity="this_week",
        evidence=[
            Evidence(
                kind="metric",
                title="Pipeline Coverage snapshot",
                url="metric://pipeline_coverage",
                snippet="Previous: 3.1x (2026-02-10), Current: 3.6x (2026-02-17)",
                timestamp="2026-02-17T08:00:00Z",
            )
        ],
        tags=["metric-anomaly", "pipeline_coverage"],
    ),
]

# ── Run the leader agent formatter ─────────────────────────────────────

operating_context = "Pricing strategy team — Q1 2026"
recap = _format_recap(signals, operating_context, top_n=5, watchlist_n=3)

print(recap)
