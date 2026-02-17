"""Demo script — generates an example Daily Strategy Recap with mock data.

Demonstrates the full 3-agent pipeline:
  Agent A (Docs) + Agent B (Metrics Intelligence) -> Agent C (Leader) -> Recap
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from packages.store.schema import (
    CrossReference,
    DriverDecomposition,
    Evidence,
    FollowUpQuery,
    MetricDelta,
    MetricsIntelligenceOutput,
    Signal,
)
from packages.agents.leader_agent import _format_recap, _cross_reference, _synthesize_verdicts

# ── Mock signals from Agent A (Docs Agent) ────────────────────────────

doc_signals = [
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
]

# ── Mock signals from Agent B (Metrics Intelligence) ──────────────────

metric_signals = [
    Signal(
        source="metrics",
        type="anomaly",
        severity=4,
        confidence=0.85,
        title="MRR Churn Rate increased 22.3%",
        summary="MRR Churn Rate went from 2.35% to 2.87% (+0.52pp, 22.3% change). Drivers: churn (68%), contraction (32%).",
        time_sensitivity="today",
        evidence=[
            Evidence(
                kind="metric",
                title="MRR Churn Rate snapshot",
                url="metric://churn_rate_mrr",
                snippet="Previous: 2.35% (2026-02-16), Current: 2.87% (2026-02-17)",
                timestamp="2026-02-17T08:00:00Z",
            )
        ],
        recommended_next_steps=[
            "Investigate why churn rate increased by 22.3% — check by segment.",
        ],
        key_questions=[
            "Is the churn rate increase expected or does it require action?",
        ],
        tags=["metric-anomaly", "churn_rate_mrr", "dataset:mrr"],
    ),
    Signal(
        source="metrics",
        type="anomaly",
        severity=3,
        confidence=0.85,
        title="Trial → Paid conversion decreased 17.1%",
        summary="Trial → Paid went from 18.7% to 15.5% (-3.2pp, 17.1% change). Self-serve conversion dropped most.",
        time_sensitivity="this_week",
        evidence=[
            Evidence(
                kind="metric",
                title="Trial → Paid snapshot",
                url="metric://trial_to_paid",
                snippet="Previous: 18.7% (2026-02-16), Current: 15.5% (2026-02-17)",
                timestamp="2026-02-17T08:00:00Z",
            )
        ],
        recommended_next_steps=[
            "Check self-serve vs sales-assisted conversion breakdown.",
        ],
        key_questions=[
            "Is the conversion drop isolated to self-serve or across all channels?",
        ],
        tags=["metric-anomaly", "trial_to_paid", "dataset:adoption"],
    ),
]

# ── Mock structured output from Agent B ───────────────────────────────

metrics_output = MetricsIntelligenceOutput(
    run_timestamp="2026-02-17T08:00:00Z",
    deltas=[
        MetricDelta("mrr_total", "Total MRR", 2850000, 2830000, 20000, 0.71, "up", "$", False),
        MetricDelta("churn_rate_mrr", "MRR Churn Rate", 2.87, 2.35, 0.52, 22.13, "up", "%", True),
        MetricDelta("docs_sent", "Documents Sent", 48750, 47200, 1550, 3.28, "up", "docs", False),
        MetricDelta("trial_to_paid", "Trial → Paid", 15.5, 18.7, -3.2, 17.11, "down", "%", True),
        MetricDelta("seat_utilization", "Seat Utilization", 71.2, 72.8, -1.6, 2.20, "down", "%", False),
        MetricDelta("dau", "Daily Active Users", 12450, 12100, 350, 2.89, "up", "users", False),
        MetricDelta("net_dollar_retention", "Net Dollar Retention", 108.5, 109.2, -0.7, 0.64, "down", "%", False),
        MetricDelta("addon_mrr", "Add-on MRR", 385000, 378000, 7000, 1.85, "up", "$", False),
    ],
    anomalies=[
        MetricDelta("churn_rate_mrr", "MRR Churn Rate", 2.87, 2.35, 0.52, 22.13, "up", "%", True, timestamp="2026-02-17T08:00:00Z"),
        MetricDelta("trial_to_paid", "Trial → Paid", 15.5, 18.7, -3.2, 17.11, "down", "%", True, timestamp="2026-02-17T08:00:00Z"),
    ],
    drivers=[
        DriverDecomposition("churn_rate_mrr", "churn", 0.35, 67.3, "Churn increased by 0.35pp, explaining 67% of the MRR Churn Rate movement — DOMINANT DRIVER"),
        DriverDecomposition("churn_rate_mrr", "contraction", 0.17, 32.7, "Contraction increased by 0.17pp, explaining 33% of the MRR Churn Rate movement"),
        DriverDecomposition("trial_to_paid", "self_serve_conversion", -2.8, 87.5, "Self-Serve Conversion decreased by 2.8pp, explaining 88% of the Trial → Paid movement — DOMINANT DRIVER"),
        DriverDecomposition("trial_to_paid", "sales_assisted_conversion", -0.4, 12.5, "Sales-Assisted Conversion decreased by 0.4pp, explaining 13% of the Trial → Paid movement"),
    ],
    segments=[],
    follow_up_queries=[
        FollowUpQuery("churn_rate_mrr", "What does the hourly trend look like for MRR Churn Rate over the past 48h?", "sql", "SELECT ...", "high", "MRR Churn Rate showed a 22.1% increase; hourly granularity will reveal if this is a spike or sustained shift."),
        FollowUpQuery("churn_rate_mrr", "Which plan segments drove the MRR Churn Rate increase?", "sql", "SELECT ...", "high", "Identify which plan segments are responsible for the increase."),
        FollowUpQuery("trial_to_paid", "What does the hourly trend look like for Trial → Paid over the past 48h?", "sql", "SELECT ...", "high", "Trial → Paid showed a 17.1% decrease; check if spike or sustained."),
    ],
)

# ── Run Agent C (Leader) ──────────────────────────────────────────────

all_signals = doc_signals + metric_signals

# Cross-reference
cross_refs = _cross_reference(doc_signals, metric_signals, metrics_output)

# Synthesize verdicts
verdicts = _synthesize_verdicts(
    doc_signals, metric_signals, cross_refs,
    metrics_output, "Pricing strategy team — Q1 2026",
    leader_cfg={"max_verdicts": 7},
)

# Format the recap
from packages.agents.leader_agent import _rank_signals
ranked = _rank_signals(all_signals)

recap = _format_recap(
    verdicts, ranked, cross_refs,
    metrics_output, "Pricing strategy team — Q1 2026",
    watchlist_n=3,
)

print(recap)
print()
print(f"--- Cross-references: {len(cross_refs)} ---")
for xr in cross_refs:
    print(f"  [{xr.relationship.upper()}] {xr.explanation}")
print()
print(f"--- Verdicts: {len(verdicts)} ---")
for v in verdicts:
    print(f"  [{v.priority.upper()}] {v.title}")
    print(f"    → {v.action}")
