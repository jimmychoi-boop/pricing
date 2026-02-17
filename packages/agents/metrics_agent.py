"""Metrics Agent — detects anomalies and trends from metric snapshots."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import yaml

from packages.store.db import RecapDB
from packages.store.schema import Evidence, MetricSnapshot, Signal

log = logging.getLogger(__name__)

DEFAULT_THRESHOLDS_CONFIG = "configs/thresholds.yaml"


def _load_thresholds(config_path: str = DEFAULT_THRESHOLDS_CONFIG) -> dict:
    with open(config_path, "r") as f:
        cfg = yaml.safe_load(f)
    return cfg.get("metric_anomaly", {})


def run(
    snapshot: dict[str, dict[str, Any]],
    db: RecapDB,
    thresholds_config: str = DEFAULT_THRESHOLDS_CONFIG,
) -> list[Signal]:
    """Analyze current metrics snapshot against previous values.

    Args:
        snapshot: output from metrics.fetch_metrics_snapshot()
        db: database handle
        thresholds_config: path to thresholds.yaml

    Returns:
        List of anomaly/trend signals.
    """
    thresholds = _load_thresholds(thresholds_config)
    pct_threshold = thresholds.get("pct_change_threshold", 15.0)
    abs_threshold = thresholds.get("abs_change_threshold", 10.0)

    now_iso = datetime.utcnow().isoformat() + "Z"
    signals: list[Signal] = []

    for metric_name, current in snapshot.items():
        current_value = current["value"]

        # Look up previous snapshot BEFORE saving the new one
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
            # First time — no delta to compute
            continue

        prev_value = prev.payload_json.get("value")
        if prev_value is None:
            continue

        # Compute delta
        try:
            current_val = float(current_value)
            prev_val = float(prev_value)
        except (TypeError, ValueError):
            continue

        abs_delta = current_val - prev_val

        if prev_val != 0:
            pct_change = abs(abs_delta / prev_val) * 100
        else:
            pct_change = 0.0

        is_anomaly = pct_change >= pct_threshold or abs(abs_delta) >= abs_threshold

        if not is_anomaly:
            continue

        # Determine direction and severity
        direction = "increased" if abs_delta > 0 else "decreased"
        display = current.get("display_name", metric_name)
        unit = current.get("unit", "")

        # Higher severity for larger deviations
        if pct_change >= pct_threshold * 2:
            severity = 5
        elif pct_change >= pct_threshold * 1.5:
            severity = 4
        else:
            severity = 3

        signals.append(
            Signal(
                source="metrics",
                type="anomaly",
                severity=severity,
                confidence=0.85,
                title=f"{display} {direction} {pct_change:.1f}%",
                summary=(
                    f"{display} went from {prev_val}{unit} to {current_val}{unit} "
                    f"({'+' if abs_delta > 0 else ''}{abs_delta}{unit}, "
                    f"{pct_change:.1f}% change)."
                ),
                time_sensitivity="today" if severity >= 4 else "this_week",
                evidence=[
                    Evidence(
                        kind="metric",
                        title=f"{display} snapshot",
                        url=f"metric://{metric_name}",
                        snippet=(
                            f"Previous: {prev_val}{unit} ({prev.snapshot_at}), "
                            f"Current: {current_val}{unit} ({now_iso})"
                        ),
                        timestamp=now_iso,
                    )
                ],
                recommended_next_steps=[
                    f"Investigate why {display} {direction} by {pct_change:.1f}%.",
                ],
                key_questions=[
                    f"Is the {display} change expected or does it require action?",
                ],
                tags=["metric-anomaly", metric_name],
            )
        )

    log.info("Metrics agent produced %d signals", len(signals))
    return signals
