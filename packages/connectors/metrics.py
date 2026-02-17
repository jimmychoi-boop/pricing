"""Metrics connector — fetches metric snapshots from Hex, warehouse, or stubs.

Supports three data source tiers:
  1. Hex saved queries  (when HEX_API_TOKEN is configured)
  2. Warehouse SQL       (when WAREHOUSE_DSN is configured — future)
  3. Stub values         (from configs/metrics.yaml, always available)

The connector reads the dataset-oriented metrics.yaml config, iterates over
all datasets and their metrics, and returns a unified snapshot dict suitable
for consumption by the Metrics Intelligence agent.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any

import yaml

from packages.connectors import hex as hex_connector

log = logging.getLogger(__name__)

DEFAULT_METRICS_CONFIG = "configs/metrics.yaml"


def _load_config(config_path: str = DEFAULT_METRICS_CONFIG) -> dict:
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def _resolve_value(metric_def: dict) -> float | int | None:
    """Resolve the current value for a metric, trying sources in priority order.

    Priority: hex_query_id > sql > stub_value
    """
    # 1. Try Hex saved query
    hex_qid = metric_def.get("hex_query_id")
    if hex_qid and hex_connector.is_available():
        rows = hex_connector.run_saved_query(hex_qid)
        if rows and len(rows) > 0:
            # Convention: saved query returns a single row with a 'value' column
            first_row = rows[0]
            val = first_row.get("value", first_row.get(list(first_row.keys())[0]))
            if val is not None:
                log.debug("Resolved %s via Hex query %s", metric_def.get("name"), hex_qid)
                return val

    # 2. Warehouse SQL — placeholder for future implementation
    # sql = metric_def.get("sql")
    # if sql and os.getenv("WAREHOUSE_DSN"):
    #     return _execute_warehouse_query(sql)

    # 3. Stub value (always available for MVP)
    return metric_def.get("stub_value")


def _resolve_driver_values(drivers: list[dict]) -> list[dict[str, Any]]:
    """Resolve current values for each driver component."""
    resolved = []
    for drv in drivers:
        value = _resolve_value(drv)
        if value is not None:
            resolved.append({
                "name": drv["name"],
                "display_name": drv.get("display_name", drv["name"]),
                "description": drv.get("description", ""),
                "value": value,
            })
    return resolved


def _resolve_segment_stubs(
    segments_def: dict[str, list[str]],
    base_value: float,
) -> dict[str, dict[str, float]]:
    """Generate stub segment breakdowns by distributing the base value.

    In production, each segment's value would come from a Hex query or SQL.
    For stubs, we distribute proportionally with some variance.
    """
    import random
    result: dict[str, dict[str, float]] = {}
    for dimension, values in segments_def.items():
        n = len(values)
        if n == 0:
            continue
        # Generate proportional weights with some randomness
        rng = random.Random(hash(dimension) + hash(str(base_value)))
        weights = [rng.uniform(0.3, 1.0) for _ in range(n)]
        total_w = sum(weights)
        result[dimension] = {
            val: round(base_value * (w / total_w), 2)
            for val, w in zip(values, weights)
        }
    return result


def fetch_metrics_snapshot(
    config_path: str = DEFAULT_METRICS_CONFIG,
) -> dict[str, dict[str, Any]]:
    """Fetch the current metrics snapshot across all datasets.

    Returns a dict keyed by metric name, each value being:
        {
            "display_name": str,
            "description": str,
            "dataset": str,
            "value": float | int,
            "unit": str,
            "timestamp": str (ISO-8601),
            "drivers": list[dict] | None,
            "segments": dict[str, dict[str, float]] | None,
            "hex_query_id": str | None,
            "hex_chart_id": str | None,
        }
    """
    cfg = _load_config(config_path)
    datasets = cfg.get("datasets", {})
    hex_cfg = cfg.get("hex", {})
    now = datetime.utcnow().isoformat() + "Z"
    snapshot: dict[str, dict[str, Any]] = {}

    for dataset_key, dataset_def in datasets.items():
        dataset_display = dataset_def.get("display_name", dataset_key)
        metrics = dataset_def.get("metrics", [])

        for m in metrics:
            name = m["name"]
            value = _resolve_value(m)
            if value is None:
                log.warning("No value resolved for metric %s, skipping", name)
                continue

            # Resolve drivers if defined
            drivers_raw = m.get("drivers", [])
            drivers = _resolve_driver_values(drivers_raw) if drivers_raw else None

            # Resolve segment breakdowns if defined
            segments_def = m.get("segments")
            segments = None
            if segments_def:
                segments = _resolve_segment_stubs(segments_def, float(value))

            snapshot[name] = {
                "display_name": m.get("display_name", name),
                "description": m.get("description", ""),
                "dataset": dataset_key,
                "dataset_display": dataset_display,
                "value": value,
                "unit": m.get("unit", ""),
                "timestamp": now,
                "drivers": drivers,
                "segments": segments,
                "hex_query_id": m.get("hex_query_id"),
                "hex_chart_id": m.get("hex_chart_id"),
            }

    log.info(
        "Fetched %d metrics across %d datasets (%s mode)",
        len(snapshot),
        len(datasets),
        "Hex" if hex_connector.is_available() else "stub",
    )
    return snapshot


def fetch_hex_chart_urls(
    config_path: str = DEFAULT_METRICS_CONFIG,
) -> dict[str, str]:
    """Return mapping of chart_name -> embed_url for configured Hex charts."""
    cfg = _load_config(config_path)
    saved_charts = cfg.get("hex", {}).get("saved_charts", {})
    urls: dict[str, str] = {}
    for chart_name, chart_id in saved_charts.items():
        url = hex_connector.get_chart_url(chart_id) if chart_id else None
        if url:
            urls[chart_name] = url
    return urls
