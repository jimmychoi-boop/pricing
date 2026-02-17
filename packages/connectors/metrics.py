"""Metrics connector — fetches metric snapshots from Hex, warehouse, or stubs.

Supports three data source tiers (checked in priority order):
  1. Hex saved queries  (when HEX_API_TOKEN is configured)
  2. Snowflake SQL       (when SNOWFLAKE_ACCOUNT is configured)
  3. Stub values         (from configs/metrics.yaml, always available)

The connector reads the dataset-oriented metrics.yaml config, iterates over
all datasets and their metrics, and returns a unified snapshot dict suitable
for consumption by the Metrics Intelligence agent.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import yaml

from packages.connectors import hex as hex_connector
from packages.connectors import warehouse

log = logging.getLogger(__name__)

DEFAULT_METRICS_CONFIG = "configs/metrics.yaml"


def _load_config(config_path: str = DEFAULT_METRICS_CONFIG) -> dict:
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def _resolve_value(metric_def: dict) -> float | int | None:
    """Resolve the current value for a metric, trying sources in priority order.

    Priority: hex_query_id > sql > stub_value
    """
    name = metric_def.get("name", "unknown")

    # 1. Try Hex saved query
    hex_qid = metric_def.get("hex_query_id")
    if hex_qid and hex_connector.is_available():
        rows = hex_connector.run_saved_query(hex_qid)
        if rows and len(rows) > 0:
            first_row = rows[0]
            val = first_row.get("value", first_row.get(list(first_row.keys())[0]))
            if val is not None:
                log.debug("Resolved %s via Hex query %s", name, hex_qid)
                return val

    # 2. Try Snowflake SQL
    sql = metric_def.get("sql")
    if sql and warehouse.is_available():
        val = warehouse.execute_scalar(sql)
        if val is not None:
            log.debug("Resolved %s via Snowflake SQL", name)
            return val
        else:
            log.warning("Snowflake SQL returned no value for %s, falling back to stub", name)

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


def _resolve_segments(
    segments_def: dict[str, Any],
    base_value: float,
) -> dict[str, dict[str, float]]:
    """Resolve segment breakdowns from SQL or stubs.

    Each segment dimension can have a sql_template or fall back to stub distribution.

    segments_def format:
      plan: ["free", "starter", "professional", "enterprise"]
      # or with SQL:
      plan:
        values: ["free", "starter", "professional", "enterprise"]
        sql: "SELECT plan_name, SUM(mrr) FROM ... GROUP BY 1"
    """
    import random
    result: dict[str, dict[str, float]] = {}

    for dimension, dim_def in segments_def.items():
        # Handle both formats: list of values or dict with sql
        if isinstance(dim_def, list):
            values = dim_def
            sql = None
        elif isinstance(dim_def, dict):
            values = dim_def.get("values", [])
            sql = dim_def.get("sql")
        else:
            continue

        # Try SQL first
        if sql and warehouse.is_available():
            seg_data = warehouse.execute_segment_query(sql)
            if seg_data:
                result[dimension] = seg_data
                log.debug("Resolved segment %s via Snowflake SQL (%d segments)", dimension, len(seg_data))
                continue

        # Fall back to stub distribution
        n = len(values)
        if n == 0:
            continue
        rng = random.Random(hash(dimension) + hash(str(base_value)))
        weights = [rng.uniform(0.3, 1.0) for _ in range(n)]
        total_w = sum(weights)
        result[dimension] = {
            val: round(base_value * (w / total_w), 2)
            for val, w in zip(values, weights)
        }

    return result


def _data_source_label() -> str:
    """Return a label describing the active data source."""
    if hex_connector.is_available():
        return "Hex"
    if warehouse.is_available():
        return "Snowflake"
    return "stub"


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
    now = datetime.utcnow().isoformat() + "Z"
    snapshot: dict[str, dict[str, Any]] = {}
    source_label = _data_source_label()

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
                segments = _resolve_segments(segments_def, float(value))

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
        len(snapshot), len(datasets), source_label,
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
