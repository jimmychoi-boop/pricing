"""Metrics Intelligence Agent (Agent B).

Ingests metric snapshots from Hex / warehouse / stubs and produces structured
intelligence output:

  1. Daily deltas + anomalies  — what moved
  2. Drivers decomposition     — why it moved
  3. Segments impacted         — who was affected (plan, SA/SS, cohort, region, channel, product line)
  4. Suggested follow-up queries — how to validate
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import yaml

from packages.connectors import hex as hex_connector
from packages.store.db import RecapDB
from packages.store.schema import (
    DriverDecomposition,
    Evidence,
    FollowUpQuery,
    MetricDelta,
    MetricSnapshot,
    MetricsIntelligenceOutput,
    SegmentImpact,
    Signal,
)

log = logging.getLogger(__name__)

DEFAULT_THRESHOLDS_CONFIG = "configs/thresholds.yaml"


# ── Configuration helpers ──────────────────────────────────────────────


def _load_thresholds(config_path: str = DEFAULT_THRESHOLDS_CONFIG) -> dict:
    with open(config_path, "r") as f:
        cfg = yaml.safe_load(f)
    return cfg.get("metrics_intelligence", {})


def _severity_from_pct(pct_change: float, bands: list[dict]) -> int:
    """Map a percentage change to a severity score using configured bands."""
    abs_pct = abs(pct_change)
    for band in bands:
        if abs_pct <= band["max_pct"]:
            return band["severity"]
    return 5


# ── Delta & anomaly detection ─────────────────────────────────────────


def _compute_deltas(
    snapshot: dict[str, dict[str, Any]],
    db: RecapDB,
    thresholds: dict,
    now_iso: str,
) -> tuple[list[MetricDelta], list[MetricDelta]]:
    """Compute daily deltas for every metric; separate anomalies.

    Returns (all_deltas, anomalies_only).
    """
    delta_cfg = thresholds.get("delta", {})
    min_pct = delta_cfg.get("min_pct_change", 1.0)
    anomaly_pct = delta_cfg.get("anomaly_pct_threshold", 15.0)
    anomaly_abs = delta_cfg.get("anomaly_abs_threshold", 10.0)

    all_deltas: list[MetricDelta] = []
    anomalies: list[MetricDelta] = []

    for metric_name, current in snapshot.items():
        current_value = current["value"]

        # Retrieve previous snapshot BEFORE persisting the new one
        prev = db.get_latest_metric_snapshot(metric_name)

        # Persist current snapshot
        db.save_metric_snapshot(
            MetricSnapshot(
                metric_name=metric_name,
                snapshot_at=now_iso,
                payload_json=current,
            )
        )

        if prev is None:
            continue

        prev_value = prev.payload_json.get("value")
        if prev_value is None:
            continue

        try:
            cur_val = float(current_value)
            prv_val = float(prev_value)
        except (TypeError, ValueError):
            continue

        abs_delta = cur_val - prv_val
        pct_change = abs(abs_delta / prv_val) * 100 if prv_val != 0 else 0.0

        if pct_change < min_pct and abs(abs_delta) < 1.0:
            continue  # too small to report

        if abs_delta > 0:
            direction = "up"
        elif abs_delta < 0:
            direction = "down"
        else:
            direction = "flat"

        is_anomaly = pct_change >= anomaly_pct or abs(abs_delta) >= anomaly_abs

        delta = MetricDelta(
            metric_name=metric_name,
            display_name=current.get("display_name", metric_name),
            current_value=cur_val,
            previous_value=prv_val,
            abs_delta=round(abs_delta, 2),
            pct_change=round(pct_change, 2),
            direction=direction,
            unit=current.get("unit", ""),
            is_anomaly=is_anomaly,
            timestamp=now_iso,
        )

        all_deltas.append(delta)
        if is_anomaly:
            anomalies.append(delta)

    log.info("Deltas: %d total, %d anomalies", len(all_deltas), len(anomalies))
    return all_deltas, anomalies


# ── Drivers decomposition ─────────────────────────────────────────────


def _decompose_drivers(
    snapshot: dict[str, dict[str, Any]],
    deltas: list[MetricDelta],
    db: RecapDB,
    thresholds: dict,
    now_iso: str,
) -> list[DriverDecomposition]:
    """For metrics with driver definitions, decompose the delta into driver-level contributions."""
    driver_cfg = thresholds.get("drivers", {})
    min_contribution_pct = driver_cfg.get("min_contribution_pct", 5.0)
    highlight_dominant = driver_cfg.get("highlight_dominant_pct", 60.0)

    decompositions: list[DriverDecomposition] = []

    # Index deltas by metric name for quick lookup
    delta_by_name = {d.metric_name: d for d in deltas}

    for metric_name, current in snapshot.items():
        drivers = current.get("drivers")
        if not drivers:
            continue

        metric_delta = delta_by_name.get(metric_name)
        if not metric_delta or metric_delta.abs_delta == 0:
            continue

        total_abs_delta = metric_delta.abs_delta

        for drv in drivers:
            drv_name = drv["name"]
            drv_value = drv["value"]

            # Get previous driver value from the stored snapshot
            prev_snap = db.get_latest_metric_snapshot(f"_driver_{metric_name}_{drv_name}")

            # Persist current driver snapshot
            db.save_metric_snapshot(
                MetricSnapshot(
                    metric_name=f"_driver_{metric_name}_{drv_name}",
                    snapshot_at=now_iso,
                    payload_json={"value": drv_value, "display_name": drv.get("display_name", drv_name)},
                )
            )

            if prev_snap is None:
                continue

            prev_drv_value = prev_snap.payload_json.get("value")
            if prev_drv_value is None:
                continue

            try:
                cur_drv = float(drv_value)
                prv_drv = float(prev_drv_value)
            except (TypeError, ValueError):
                continue

            driver_delta = cur_drv - prv_drv
            pct_of_total = (
                abs(driver_delta / total_abs_delta) * 100
                if total_abs_delta != 0
                else 0.0
            )

            if pct_of_total < min_contribution_pct:
                continue

            evidence = []
            hex_qid = current.get("hex_query_id")
            if hex_qid:
                chart_url = hex_connector.get_chart_url(hex_qid)
                if chart_url:
                    evidence.append(Evidence(
                        kind="hex",
                        title=f"{drv.get('display_name', drv_name)} trend",
                        url=chart_url,
                        timestamp=now_iso,
                    ))

            direction_word = "increased" if driver_delta > 0 else "decreased"
            is_dominant = pct_of_total >= highlight_dominant

            decompositions.append(
                DriverDecomposition(
                    metric_name=metric_name,
                    driver_name=drv_name,
                    contribution=round(driver_delta, 2),
                    pct_of_delta=round(pct_of_total, 2),
                    description=(
                        f"{drv.get('display_name', drv_name)} {direction_word} by "
                        f"{abs(driver_delta):.1f}, explaining {pct_of_total:.0f}% of the "
                        f"{metric_delta.display_name} movement"
                        + (" — DOMINANT DRIVER" if is_dominant else "")
                    ),
                    evidence=evidence,
                )
            )

    log.info("Driver decompositions: %d", len(decompositions))
    return decompositions


# ── Segment impact analysis ───────────────────────────────────────────


def _analyze_segments(
    snapshot: dict[str, dict[str, Any]],
    deltas: list[MetricDelta],
    db: RecapDB,
    thresholds: dict,
    now_iso: str,
) -> list[SegmentImpact]:
    """For metrics with segment breakdowns, identify disproportionately impacted segments."""
    seg_cfg = thresholds.get("segments", {})
    outsized_mult = seg_cfg.get("outsized_multiplier", 2.0)
    min_abs_delta = seg_cfg.get("min_segment_abs_delta", 5.0)

    impacts: list[SegmentImpact] = []
    delta_by_name = {d.metric_name: d for d in deltas}

    for metric_name, current in snapshot.items():
        segments = current.get("segments")
        if not segments:
            continue

        metric_delta = delta_by_name.get(metric_name)
        overall_pct = metric_delta.pct_change if metric_delta else 0.0

        for dimension, seg_values in segments.items():
            for seg_name, seg_current_val in seg_values.items():
                # Get previous segment value
                seg_key = f"_seg_{metric_name}_{dimension}_{seg_name}"
                prev_snap = db.get_latest_metric_snapshot(seg_key)

                # Persist current segment snapshot
                db.save_metric_snapshot(
                    MetricSnapshot(
                        metric_name=seg_key,
                        snapshot_at=now_iso,
                        payload_json={"value": seg_current_val},
                    )
                )

                if prev_snap is None:
                    continue

                prev_seg_val = prev_snap.payload_json.get("value")
                if prev_seg_val is None:
                    continue

                try:
                    cur_s = float(seg_current_val)
                    prv_s = float(prev_seg_val)
                except (TypeError, ValueError):
                    continue

                seg_delta = cur_s - prv_s
                seg_pct = abs(seg_delta / prv_s) * 100 if prv_s != 0 else 0.0

                if abs(seg_delta) < min_abs_delta:
                    continue

                is_outsized = seg_pct >= (overall_pct * outsized_mult) if overall_pct > 0 else seg_pct > 20

                if seg_delta > 0:
                    direction = "up"
                elif seg_delta < 0:
                    direction = "down"
                else:
                    direction = "flat"

                direction_word = "increased" if seg_delta > 0 else "decreased"
                description = (
                    f"{current.get('display_name', metric_name)} in "
                    f"{seg_name} ({dimension}) {direction_word} by "
                    f"{seg_pct:.1f}% vs {overall_pct:.1f}% overall"
                )
                if is_outsized:
                    description += " — OUTSIZED MOVEMENT"

                impacts.append(
                    SegmentImpact(
                        metric_name=metric_name,
                        dimension=dimension,
                        segment_value=seg_name,
                        segment_delta=round(seg_delta, 2),
                        segment_pct_change=round(seg_pct, 2),
                        segment_current=round(cur_s, 2),
                        direction=direction,
                        is_outsized=is_outsized,
                        description=description,
                    )
                )

    log.info("Segment impacts: %d total, %d outsized",
             len(impacts), sum(1 for s in impacts if s.is_outsized))
    return impacts


# ── Follow-up query generation ────────────────────────────────────────


def _generate_follow_ups(
    anomalies: list[MetricDelta],
    drivers: list[DriverDecomposition],
    segments: list[SegmentImpact],
    snapshot: dict[str, dict[str, Any]],
    thresholds: dict,
) -> list[FollowUpQuery]:
    """Generate suggested SQL/Hex queries to validate anomalies and drill in."""
    fu_cfg = thresholds.get("follow_ups", {})
    max_per_anomaly = fu_cfg.get("max_queries_per_anomaly", 3)
    max_total = fu_cfg.get("max_total_queries", 10)

    queries: list[FollowUpQuery] = []

    for anomaly in anomalies:
        metric_info = snapshot.get(anomaly.metric_name, {})
        display = anomaly.display_name
        direction = "increase" if anomaly.direction == "up" else "decrease"
        count = 0

        # 1. Time-series drill-down
        queries.append(FollowUpQuery(
            metric_name=anomaly.metric_name,
            question=f"What does the hourly trend look like for {display} over the past 48h?",
            query_type="sql",
            query_text=(
                f"SELECT date_trunc('hour', event_time) AS hour, "
                f"COUNT(*) AS value "
                f"FROM events "
                f"WHERE metric_name = '{anomaly.metric_name}' "
                f"AND event_time >= NOW() - INTERVAL '48 hours' "
                f"GROUP BY 1 ORDER BY 1"
            ),
            priority="high",
            rationale=f"{display} showed a {anomaly.pct_change:.1f}% {direction}; "
                      f"hourly granularity will reveal if this is a spike or sustained shift.",
        ))
        count += 1

        # 2. Segment breakdown query (if segments are available)
        if metric_info.get("segments") and count < max_per_anomaly:
            first_dim = list(metric_info["segments"].keys())[0]
            queries.append(FollowUpQuery(
                metric_name=anomaly.metric_name,
                question=f"Which {first_dim} segments drove the {display} {direction}?",
                query_type="sql",
                query_text=(
                    f"SELECT {first_dim}, SUM(value) AS total, "
                    f"SUM(value) - LAG(SUM(value)) OVER (ORDER BY {first_dim}) AS delta "
                    f"FROM metrics "
                    f"WHERE metric_name = '{anomaly.metric_name}' "
                    f"AND snapshot_date IN (CURRENT_DATE, CURRENT_DATE - 1) "
                    f"GROUP BY {first_dim}, snapshot_date "
                    f"ORDER BY delta DESC"
                ),
                priority="high",
                rationale=f"Identify which {first_dim} segments are responsible for the {direction}.",
            ))
            count += 1

        # 3. Correlation query
        if count < max_per_anomaly:
            queries.append(FollowUpQuery(
                metric_name=anomaly.metric_name,
                question=f"Are there correlated metrics that also moved with {display}?",
                query_type="sql",
                query_text=(
                    f"SELECT metric_name, "
                    f"(current_value - previous_value) / NULLIF(previous_value, 0) * 100 AS pct_change "
                    f"FROM metric_deltas "
                    f"WHERE snapshot_date = CURRENT_DATE "
                    f"AND ABS(pct_change) > 5 "
                    f"ORDER BY ABS(pct_change) DESC LIMIT 10"
                ),
                priority="medium",
                rationale=f"Check whether the {display} {direction} is isolated or part of a broader pattern.",
            ))

        if len(queries) >= max_total:
            break

    # Add follow-ups for outsized segment movements
    outsized = [s for s in segments if s.is_outsized]
    for seg in outsized[:3]:
        if len(queries) >= max_total:
            break
        queries.append(FollowUpQuery(
            metric_name=seg.metric_name,
            question=(
                f"Why did {seg.segment_value} ({seg.dimension}) move "
                f"{seg.segment_pct_change:.1f}% vs {seg.segment_pct_change / 2:.1f}% overall?"
            ),
            query_type="sql",
            query_text=(
                f"SELECT date_trunc('day', event_time) AS day, "
                f"COUNT(*) AS value "
                f"FROM events "
                f"WHERE metric_name = '{seg.metric_name}' "
                f"AND {seg.dimension} = '{seg.segment_value}' "
                f"AND event_time >= NOW() - INTERVAL '7 days' "
                f"GROUP BY 1 ORDER BY 1"
            ),
            priority="medium",
            rationale=seg.description,
        ))

    log.info("Generated %d follow-up queries", len(queries))
    return queries[:max_total]


# ── Signal conversion ─────────────────────────────────────────────────


def _to_signals(
    output: MetricsIntelligenceOutput,
    thresholds: dict,
) -> list[Signal]:
    """Convert MetricsIntelligenceOutput into Signal objects for the Leader Agent."""
    severity_bands = thresholds.get("severity_bands", [
        {"max_pct": 5, "severity": 1},
        {"max_pct": 10, "severity": 2},
        {"max_pct": 20, "severity": 3},
        {"max_pct": 35, "severity": 4},
        {"max_pct": 999999, "severity": 5},
    ])

    signals: list[Signal] = []

    for anomaly in output.anomalies:
        direction_word = "increased" if anomaly.direction == "up" else "decreased"
        severity = _severity_from_pct(anomaly.pct_change, severity_bands)

        # Collect driver context for this metric
        metric_drivers = [d for d in output.drivers if d.metric_name == anomaly.metric_name]
        driver_summary = ""
        if metric_drivers:
            top_drivers = sorted(metric_drivers, key=lambda d: abs(d.contribution), reverse=True)[:3]
            parts = [f"{d.driver_name} ({d.pct_of_delta:.0f}%)" for d in top_drivers]
            driver_summary = f" Drivers: {', '.join(parts)}."

        # Collect outsized segment context
        outsized_segs = [
            s for s in output.segments
            if s.metric_name == anomaly.metric_name and s.is_outsized
        ]
        seg_summary = ""
        if outsized_segs:
            parts = [f"{s.segment_value} ({s.dimension}: {s.segment_pct_change:.0f}%)" for s in outsized_segs[:3]]
            seg_summary = f" Outsized segments: {', '.join(parts)}."

        # Collect follow-up queries
        metric_queries = [q for q in output.follow_up_queries if q.metric_name == anomaly.metric_name]
        next_steps = [q.question for q in metric_queries[:2]]
        key_questions = [
            f"Is the {anomaly.display_name} {direction_word.replace('ed', 'e')} expected or concerning?"
        ]

        # Build evidence
        evidence = [
            Evidence(
                kind="metric",
                title=f"{anomaly.display_name} snapshot",
                url=f"metric://{anomaly.metric_name}",
                snippet=(
                    f"Previous: {anomaly.previous_value}{anomaly.unit}, "
                    f"Current: {anomaly.current_value}{anomaly.unit} "
                    f"({'+' if anomaly.abs_delta > 0 else ''}{anomaly.abs_delta}{anomaly.unit}, "
                    f"{anomaly.pct_change:.1f}%)"
                ),
                timestamp=anomaly.timestamp,
            )
        ]
        # Add Hex evidence from drivers
        for d in metric_drivers:
            evidence.extend(d.evidence)

        signals.append(
            Signal(
                source="metrics",
                type="anomaly",
                severity=severity,
                confidence=0.85,
                title=f"{anomaly.display_name} {direction_word} {anomaly.pct_change:.1f}%",
                summary=(
                    f"{anomaly.display_name} went from {anomaly.previous_value}{anomaly.unit} "
                    f"to {anomaly.current_value}{anomaly.unit} "
                    f"({'+' if anomaly.abs_delta > 0 else ''}{anomaly.abs_delta}{anomaly.unit}, "
                    f"{anomaly.pct_change:.1f}% change)."
                    f"{driver_summary}{seg_summary}"
                ),
                time_sensitivity="today" if severity >= 4 else "this_week",
                evidence=evidence,
                recommended_next_steps=next_steps or [
                    f"Investigate why {anomaly.display_name} {direction_word} by {anomaly.pct_change:.1f}%.",
                ],
                key_questions=key_questions,
                tags=["metric-anomaly", anomaly.metric_name,
                      f"dataset:{anomaly.metric_name.split('_')[0]}"],
            )
        )

    return signals


# ── Public entry point ─────────────────────────────────────────────────


def run(
    snapshot: dict[str, dict[str, Any]],
    db: RecapDB,
    thresholds_config: str = DEFAULT_THRESHOLDS_CONFIG,
) -> tuple[list[Signal], MetricsIntelligenceOutput]:
    """Run the Metrics Intelligence agent.

    Args:
        snapshot: output from metrics.fetch_metrics_snapshot()
        db: database handle for snapshot persistence and history lookups
        thresholds_config: path to thresholds.yaml

    Returns:
        (signals_for_leader, structured_intelligence_output)
    """
    thresholds = _load_thresholds(thresholds_config)
    now_iso = datetime.utcnow().isoformat() + "Z"

    # 1. Compute daily deltas + detect anomalies
    all_deltas, anomalies = _compute_deltas(snapshot, db, thresholds, now_iso)

    # 2. Decompose drivers for metrics that moved
    drivers = _decompose_drivers(snapshot, all_deltas, db, thresholds, now_iso)

    # 3. Analyze segment impacts
    segments = _analyze_segments(snapshot, all_deltas, db, thresholds, now_iso)

    # 4. Generate follow-up queries for anomalies
    follow_ups = _generate_follow_ups(anomalies, drivers, segments, snapshot, thresholds)

    # Package structured output
    output = MetricsIntelligenceOutput(
        run_timestamp=now_iso,
        deltas=all_deltas,
        anomalies=anomalies,
        drivers=drivers,
        segments=segments,
        follow_up_queries=follow_ups,
    )

    # Convert to signals for the Leader Agent
    signals = _to_signals(output, thresholds)

    log.info(
        "Metrics Intelligence agent: %d deltas, %d anomalies, "
        "%d drivers, %d segment impacts, %d follow-ups → %d signals",
        len(all_deltas), len(anomalies), len(drivers),
        len(segments), len(follow_ups), len(signals),
    )

    return signals, output
