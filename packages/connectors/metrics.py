"""Metrics connector — stub implementation for MVP.

Loads metric definitions from configs/metrics.yaml and returns stub values.
Replace stub_value with real SQL queries or Hex notebook integration later.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import yaml

log = logging.getLogger(__name__)

DEFAULT_METRICS_CONFIG = "configs/metrics.yaml"


def _load_metrics_config(config_path: str = DEFAULT_METRICS_CONFIG) -> list[dict]:
    with open(config_path, "r") as f:
        cfg = yaml.safe_load(f)
    return cfg.get("metrics", [])


def fetch_metrics_snapshot(
    config_path: str = DEFAULT_METRICS_CONFIG,
) -> dict[str, dict[str, Any]]:
    """Fetch the current metrics snapshot.

    Returns a dict keyed by metric name, each value being:
        {
            "display_name": str,
            "description": str,
            "value": float | int,
            "unit": str,
            "timestamp": str (ISO-8601),
        }

    MVP: returns stub_value from the YAML config.
    Future: execute SQL or call Hex API for real values.
    """
    metrics = _load_metrics_config(config_path)
    now = datetime.utcnow().isoformat() + "Z"
    snapshot: dict[str, dict[str, Any]] = {}

    for m in metrics:
        name = m["name"]
        # Future: if m.get("sql"), run the query against the warehouse
        value = m.get("stub_value", 0)
        snapshot[name] = {
            "display_name": m.get("display_name", name),
            "description": m.get("description", ""),
            "value": value,
            "unit": m.get("unit", ""),
            "timestamp": now,
        }

    log.info("Fetched %d metric values (stub mode)", len(snapshot))
    return snapshot
