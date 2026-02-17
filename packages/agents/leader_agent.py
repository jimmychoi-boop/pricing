"""Leader Agent (Agent C) — cross-references Agent A + B, synthesizes verdicts, makes the call.

Pipeline:
  1. Receive signals from Docs Agent (A) and structured output from Metrics Intelligence (B)
  2. Cross-reference: find doc signals that correlate with metric movements
  3. Synthesize verdicts: combine correlated signals into actionable calls
  4. Rank & format: produce the final daily recap with verdicts, evidence, and next steps
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime

import yaml
from rapidfuzz import fuzz

from packages.store.schema import (
    CrossReference,
    Evidence,
    FollowUpQuery,
    MetricsIntelligenceOutput,
    RecapOutput,
    Signal,
    Verdict,
)

log = logging.getLogger(__name__)

DEFAULT_THRESHOLDS_CONFIG = "configs/thresholds.yaml"

# Time-sensitivity sort order (lower = more urgent)
TIME_SENSITIVITY_ORDER = {
    "today": 0,
    "this_week": 1,
    "this_month": 2,
    None: 3,
}

# Keywords that indicate a doc signal might relate to a metric
_METRIC_KEYWORD_MAP = {
    "mrr": ["mrr", "revenue", "arr", "recurring"],
    "churn": ["churn", "cancel", "attrit", "retention", "lost customer"],
    "usage": ["usage", "docs sent", "documents", "volume", "sends", "adoption"],
    "seats": ["seat", "license", "user count", "provision"],
    "conversion": ["conversion", "trial", "free to paid", "signup", "funnel", "activation"],
    "expansion": ["expansion", "upsell", "upgrade", "add-on", "addon"],
    "contraction": ["contraction", "downgrade", "reduce"],
    "ndr": ["net dollar retention", "ndr", "dollar retention"],
}


def _load_thresholds(config_path: str = DEFAULT_THRESHOLDS_CONFIG) -> dict:
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


# ── 1. Deduplication ──────────────────────────────────────────────────


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


# ── 2. Cross-referencing ─────────────────────────────────────────────


def _signal_matches_metric(signal: Signal, metric_name: str) -> tuple[bool, str]:
    """Check if a doc signal's text relates to a given metric name.

    Returns (is_match, relationship_type).
    """
    text = (signal.title + " " + signal.summary).lower()
    metric_lower = metric_name.lower()

    # Direct mention of the metric name
    if metric_lower.replace("_", " ") in text or metric_lower in text:
        return True, "confirms"

    # Keyword-based matching
    for keyword_group, keywords in _METRIC_KEYWORD_MAP.items():
        if keyword_group in metric_lower:
            for kw in keywords:
                if kw in text:
                    return True, "confirms"

    return False, "unrelated"


def _cross_reference(
    doc_signals: list[Signal],
    metric_signals: list[Signal],
    metrics_output: MetricsIntelligenceOutput | None,
) -> list[CrossReference]:
    """Find connections between doc signals and metric signals/anomalies."""
    cross_refs: list[CrossReference] = []

    if not metrics_output:
        return cross_refs

    # Build a lookup from metric_name to metric signal
    metric_sig_by_name: dict[str, Signal] = {}
    for ms in metric_signals:
        for tag in ms.tags:
            if tag.startswith("metric-anomaly"):
                continue
            if tag.startswith("dataset:"):
                continue
            metric_sig_by_name[tag] = ms

    # For each anomaly, check if any doc signals correlate
    for anomaly in metrics_output.anomalies:
        for doc_sig in doc_signals:
            matches, relationship = _signal_matches_metric(doc_sig, anomaly.metric_name)
            if not matches:
                continue

            # Look up the corresponding metric signal
            matching_metric_sig = metric_sig_by_name.get(anomaly.metric_name)

            # Combine evidence from both
            combined_evidence = list(doc_sig.evidence)
            if matching_metric_sig:
                combined_evidence.extend(matching_metric_sig.evidence)

            # Boost severity when doc + metric agree
            combined_severity = min(5, max(doc_sig.severity, anomaly.pct_change // 10 + 3))

            # Check if doc contradicts the metric direction
            doc_text_lower = (doc_sig.title + " " + doc_sig.summary).lower()
            if anomaly.direction == "up" and any(w in doc_text_lower for w in ["decrease", "drop", "decline", "down"]):
                relationship = "contradicts"
            elif anomaly.direction == "down" and any(w in doc_text_lower for w in ["increase", "growth", "up", "rise"]):
                relationship = "contradicts"

            cross_refs.append(CrossReference(
                doc_signal_id=doc_sig.signal_id,
                metric_signal_id=matching_metric_sig.signal_id if matching_metric_sig else None,
                metric_name=anomaly.metric_name,
                relationship=relationship,
                explanation=(
                    f'Doc signal "{doc_sig.title}" {relationship} metric '
                    f'"{anomaly.display_name}" ({anomaly.direction} {anomaly.pct_change:.1f}%)'
                ),
                combined_severity=int(combined_severity),
                combined_evidence=combined_evidence,
            ))

    log.info("Cross-referencing found %d correlations", len(cross_refs))
    return cross_refs


# ── 3. Verdict synthesis ─────────────────────────────────────────────


def _synthesize_verdicts(
    doc_signals: list[Signal],
    metric_signals: list[Signal],
    cross_refs: list[CrossReference],
    metrics_output: MetricsIntelligenceOutput | None,
    operating_context: str,
    leader_cfg: dict,
) -> list[Verdict]:
    """Synthesize verdicts by combining correlated signals and making calls."""
    verdicts: list[Verdict] = []
    max_verdicts = leader_cfg.get("max_verdicts", 7)

    # Index signals by ID for lookup
    all_signals = doc_signals + metric_signals
    signal_by_id = {s.signal_id: s for s in all_signals}

    # Track which signals have been consumed by a cross-reference verdict
    consumed_signal_ids: set[str] = set()

    # Priority 1: Build verdicts from cross-references (strongest — two agents agree)
    for xref in cross_refs:
        if len(verdicts) >= max_verdicts:
            break

        doc_sig = signal_by_id.get(xref.doc_signal_id)
        metric_sig = signal_by_id.get(xref.metric_signal_id) if xref.metric_signal_id else None
        if not doc_sig:
            continue

        consumed_signal_ids.add(xref.doc_signal_id)
        if xref.metric_signal_id:
            consumed_signal_ids.add(xref.metric_signal_id)

        # Gather relevant drivers and follow-ups from metrics output
        related_follow_ups: list[FollowUpQuery] = []
        driver_context = ""
        if metrics_output and xref.metric_name:
            drivers = [d for d in metrics_output.drivers if d.metric_name == xref.metric_name]
            if drivers:
                top = sorted(drivers, key=lambda d: abs(d.contribution), reverse=True)[:2]
                parts = [f"{d.driver_name} ({d.pct_of_delta:.0f}%)" for d in top]
                driver_context = f" Primary drivers: {', '.join(parts)}."

            related_follow_ups = [
                q for q in metrics_output.follow_up_queries
                if q.metric_name == xref.metric_name
            ][:2]

        # Build the narrative
        if xref.relationship == "confirms":
            narrative = (
                f'The docs and the data agree: "{doc_sig.title}" is backed by a '
                f'{xref.metric_name} movement.{driver_context} '
                f'This warrants immediate attention.'
            )
        elif xref.relationship == "contradicts":
            narrative = (
                f'The docs say one thing but the data says another. '
                f'"{doc_sig.title}" conflicts with what {xref.metric_name} shows. '
                f'Validate which signal is accurate before acting.'
            )
        else:
            narrative = (
                f'"{doc_sig.title}" may be related to {xref.metric_name} '
                f'movement.{driver_context} Worth investigating.'
            )

        # Determine action
        action = doc_sig.recommended_next_steps[0] if doc_sig.recommended_next_steps else ""
        if not action and metric_sig and metric_sig.recommended_next_steps:
            action = metric_sig.recommended_next_steps[0]
        if not action:
            action = f"Review the {xref.metric_name} data and the linked document together."

        priority = "critical" if xref.combined_severity >= 5 else \
                   "high" if xref.combined_severity >= 4 else \
                   "medium" if xref.combined_severity >= 3 else "low"

        supporting = [xref.doc_signal_id]
        if xref.metric_signal_id:
            supporting.append(xref.metric_signal_id)

        verdicts.append(Verdict(
            priority=priority,
            title=doc_sig.title,
            narrative=narrative,
            action=action,
            owner=doc_sig.owner,
            deadline=doc_sig.time_sensitivity,
            supporting_signals=supporting,
            cross_references=[xref],
            follow_up_queries=related_follow_ups,
            tags=doc_sig.tags + (["cross-referenced", xref.relationship]),
        ))

    # Priority 2: High-severity standalone signals not consumed by cross-refs
    remaining = [
        s for s in all_signals
        if s.signal_id not in consumed_signal_ids and s.severity >= 4
    ]
    remaining.sort(key=lambda s: (-s.severity, TIME_SENSITIVITY_ORDER.get(s.time_sensitivity, 3)))

    for sig in remaining:
        if len(verdicts) >= max_verdicts:
            break

        consumed_signal_ids.add(sig.signal_id)

        # Check if this is a metric signal with driver/segment context
        driver_context = ""
        related_follow_ups = []
        if sig.source == "metrics" and metrics_output:
            for tag in sig.tags:
                if tag.startswith("metric-anomaly") or tag.startswith("dataset:"):
                    continue
                drivers = [d for d in metrics_output.drivers if d.metric_name == tag]
                if drivers:
                    top = sorted(drivers, key=lambda d: abs(d.contribution), reverse=True)[:2]
                    parts = [f"{d.driver_name} ({d.pct_of_delta:.0f}%)" for d in top]
                    driver_context = f" Drivers: {', '.join(parts)}."
                related_follow_ups = [
                    q for q in metrics_output.follow_up_queries if q.metric_name == tag
                ][:2]

        narrative = sig.summary + driver_context
        action = sig.recommended_next_steps[0] if sig.recommended_next_steps else \
                 f"Investigate: {sig.title}"

        priority = "critical" if sig.severity >= 5 else "high"

        verdicts.append(Verdict(
            priority=priority,
            title=sig.title,
            narrative=narrative,
            action=action,
            owner=sig.owner,
            deadline=sig.time_sensitivity,
            supporting_signals=[sig.signal_id],
            follow_up_queries=related_follow_ups,
            tags=sig.tags + ["standalone"],
        ))

    # Priority 3: Fill remaining slots with medium-severity signals
    remaining_medium = [
        s for s in all_signals
        if s.signal_id not in consumed_signal_ids and s.severity >= 3
    ]
    remaining_medium.sort(key=lambda s: (-s.severity, -s.confidence))

    for sig in remaining_medium:
        if len(verdicts) >= max_verdicts:
            break

        consumed_signal_ids.add(sig.signal_id)

        verdicts.append(Verdict(
            priority="medium",
            title=sig.title,
            narrative=sig.summary,
            action=sig.recommended_next_steps[0] if sig.recommended_next_steps else "",
            owner=sig.owner,
            deadline=sig.time_sensitivity,
            supporting_signals=[sig.signal_id],
            tags=sig.tags + ["standalone"],
        ))

    log.info("Synthesized %d verdicts", len(verdicts))
    return verdicts


# ── 4. Ranking ────────────────────────────────────────────────────────


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


# ── 5. Formatting ────────────────────────────────────────────────────


PRIORITY_EMOJI = {
    "critical": ":red_circle:",
    "high": ":large_orange_circle:",
    "medium": ":large_yellow_circle:",
    "low": ":white_circle:",
}


def _format_recap(
    verdicts: list[Verdict],
    ranked: list[Signal],
    cross_refs: list[CrossReference],
    metrics_output: MetricsIntelligenceOutput | None,
    operating_context: str,
    watchlist_n: int = 3,
) -> str:
    """Build the final Slack-ready recap from verdicts + supporting data."""
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = []

    lines.append(f":newspaper:  *Daily Strategy Recap* — {now}")
    lines.append("=" * 55)
    lines.append("")

    # ── Verdicts (the calls) ──────────────────────────────────────────
    if verdicts:
        lines.append(":dart:  *Today's Calls*")
        lines.append("")

        for i, v in enumerate(verdicts, 1):
            emoji = PRIORITY_EMOJI.get(v.priority, ":white_circle:")
            lines.append(f"{i}. {emoji} *{v.title}*  [{v.priority.upper()}]")
            lines.append(f"   {v.narrative[:300]}")

            # Show cross-reference context if present
            for xref in v.cross_references:
                rel_label = {
                    "confirms": "Data confirms",
                    "contradicts": "Data contradicts",
                    "adds_context": "Related data",
                }.get(xref.relationship, "Related")
                lines.append(f"   :link: _{rel_label}:_ {xref.explanation[:150]}")

            if v.action:
                lines.append(f"   :arrow_right: *Action:* {v.action}")
            if v.owner:
                lines.append(f"   :bust_in_silhouette: *Owner:* {v.owner}")
            if v.deadline:
                lines.append(f"   :clock3: *By:* {v.deadline}")

            # Show evidence links
            for xref in v.cross_references:
                for ev in xref.combined_evidence[:2]:
                    lines.append(f"   :paperclip: <{ev.url}|{ev.title}>")

            lines.append("")

    # ── Metrics snapshot ──────────────────────────────────────────────
    if metrics_output and metrics_output.deltas:
        lines.append(":chart_with_upwards_trend:  *Metrics Snapshot*")
        lines.append("")

        # Show top movers (anomalies first, then notable deltas)
        movers = sorted(
            metrics_output.deltas,
            key=lambda d: abs(d.pct_change),
            reverse=True,
        )[:8]

        for d in movers:
            arrow = ":arrow_up:" if d.direction == "up" else ":arrow_down:" if d.direction == "down" else ":left_right_arrow:"
            anomaly_flag = " :rotating_light:" if d.is_anomaly else ""
            sign = "+" if d.abs_delta > 0 else ""
            lines.append(
                f"  {arrow} *{d.display_name}*: {d.current_value}{d.unit} "
                f"({sign}{d.abs_delta}{d.unit}, {d.pct_change:.1f}%){anomaly_flag}"
            )

        lines.append("")

    # ── Key questions ─────────────────────────────────────────────────
    questions_by_owner: dict[str, list[str]] = defaultdict(list)
    for sig in ranked:
        owner = sig.owner or "Team"
        for q in sig.key_questions:
            if q not in questions_by_owner[owner]:
                questions_by_owner[owner].append(q)

    if questions_by_owner:
        lines.append(":question:  *Key Questions*")
        lines.append("")
        for owner, qs in questions_by_owner.items():
            lines.append(f"  *{owner}:*")
            for q in qs[:3]:
                lines.append(f"   • {q}")
            lines.append("")

    # ── Follow-up queries ─────────────────────────────────────────────
    if metrics_output and metrics_output.follow_up_queries:
        high_priority = [q for q in metrics_output.follow_up_queries if q.priority == "high"]
        if high_priority:
            lines.append(":mag:  *Suggested Follow-ups*")
            lines.append("")
            for q in high_priority[:3]:
                lines.append(f"  • {q.question}")
                lines.append(f"    _{q.rationale[:120]}_")
            lines.append("")

    # ── Watchlist ─────────────────────────────────────────────────────
    consumed_ids = set()
    for v in verdicts:
        consumed_ids.update(v.supporting_signals)

    watchlist = [s for s in ranked if s.signal_id not in consumed_ids][:watchlist_n]

    if watchlist:
        lines.append(":eyes:  *Watchlist*")
        lines.append("")
        for sig in watchlist:
            lines.append(f"  • *{sig.title}* — {sig.summary[:120]}")
            if sig.evidence:
                lines.append(f"    :link: <{sig.evidence[0].url}|{sig.evidence[0].title}>")
        lines.append("")

    # ── Footer ────────────────────────────────────────────────────────
    n_xrefs = len(cross_refs)
    n_verdicts = len(verdicts)
    lines.append(
        f"_{n_verdicts} verdicts from {n_xrefs} cross-references. "
        f"All items are evidence-backed._"
    )

    return "\n".join(lines)


# ── Public entry point ─────────────────────────────────────────────────


def run(
    signals: list[Signal],
    operating_context: str,
    metrics_output: MetricsIntelligenceOutput | None = None,
    thresholds_config: str = DEFAULT_THRESHOLDS_CONFIG,
) -> tuple[list[Signal], str, RecapOutput]:
    """Cross-reference, synthesize, rank, and format the final recap.

    Args:
        signals: combined signals from Agent A (docs) and Agent B (metrics)
        operating_context: contents of context/operating_context.md
        metrics_output: structured output from Agent B (for cross-referencing)
        thresholds_config: path to thresholds.yaml

    Returns:
        (ranked_signals, recap_text, structured_recap_output)
    """
    cfg = _load_thresholds(thresholds_config)
    min_severity = cfg.get("min_severity", 2)
    min_confidence = cfg.get("min_confidence", 0.3)
    dedup_sim = cfg.get("dedup_similarity", 80)
    watchlist_n = cfg.get("watchlist_items", 3)
    leader_cfg = cfg.get("leader", {})

    now_iso = datetime.utcnow().isoformat() + "Z"

    # Filter
    filtered = [
        s for s in signals
        if s.severity >= min_severity and s.confidence >= min_confidence
    ]
    log.info(
        "Filtered %d -> %d signals (min_severity=%d, min_confidence=%.1f)",
        len(signals), len(filtered), min_severity, min_confidence,
    )

    # Deduplicate
    deduped = _deduplicate(filtered, similarity_threshold=dedup_sim)

    # Split by source
    doc_signals = [s for s in deduped if s.source == "docs"]
    metric_signals = [s for s in deduped if s.source == "metrics"]

    # Cross-reference Agent A ↔ Agent B
    cross_refs = _cross_reference(doc_signals, metric_signals, metrics_output)

    # Synthesize verdicts
    verdicts = _synthesize_verdicts(
        doc_signals, metric_signals, cross_refs,
        metrics_output, operating_context, leader_cfg,
    )

    # Rank all signals (for watchlist and questions)
    ranked = _rank_signals(deduped)

    # Format the final recap
    recap = _format_recap(
        verdicts, ranked, cross_refs,
        metrics_output, operating_context,
        watchlist_n=watchlist_n,
    )

    # Package structured output
    output = RecapOutput(
        run_timestamp=now_iso,
        verdicts=verdicts,
        cross_references=cross_refs,
        ranked_signals=ranked,
        recap_text=recap,
    )

    log.info(
        "Leader agent: %d signals -> %d cross-refs -> %d verdicts",
        len(deduped), len(cross_refs), len(verdicts),
    )

    return ranked, recap, output
