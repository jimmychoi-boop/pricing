"""Hex connector — integration with Hex saved queries, charts, and data models.

Supports two modes:
  1. API mode: calls Hex REST API to run saved queries and fetch results
     (requires HEX_API_TOKEN and HEX_WORKSPACE_ID env vars)
  2. Stub mode: returns None, signaling the caller to fall back to stub data

Hex API docs: https://learn.hex.tech/docs/develop-logic/hex-api/api-reference
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import requests

log = logging.getLogger(__name__)

_HEX_API_BASE = "https://app.hex.tech/api/v1"
_MAX_POLL_SECONDS = 120
_POLL_INTERVAL = 3


def _get_credentials() -> tuple[str | None, str | None]:
    """Read Hex credentials from environment."""
    token = os.getenv("HEX_API_TOKEN")
    workspace = os.getenv("HEX_WORKSPACE_ID")
    return token, workspace


def is_available() -> bool:
    """Return True if Hex API credentials are configured."""
    token, workspace = _get_credentials()
    return bool(token and workspace)


def run_saved_query(query_id: str) -> list[dict[str, Any]] | None:
    """Execute a Hex saved query and return rows as list of dicts.

    Returns None if Hex is not configured or the query fails.
    """
    token, workspace = _get_credentials()
    if not token or not workspace or not query_id:
        return None

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    # Trigger a run
    run_url = f"{_HEX_API_BASE}/workspace/{workspace}/query/{query_id}/run"
    try:
        resp = requests.post(run_url, headers=headers, timeout=30)
        resp.raise_for_status()
        run_data = resp.json()
        run_id = run_data.get("runId")
    except Exception as exc:
        log.warning("Hex: failed to trigger query %s: %s", query_id, exc)
        return None

    if not run_id:
        log.warning("Hex: no runId returned for query %s", query_id)
        return None

    # Poll for completion
    status_url = f"{_HEX_API_BASE}/workspace/{workspace}/query/{query_id}/run/{run_id}"
    elapsed = 0
    while elapsed < _MAX_POLL_SECONDS:
        try:
            resp = requests.get(status_url, headers=headers, timeout=15)
            resp.raise_for_status()
            status_data = resp.json()
            status = status_data.get("status")
            if status == "COMPLETED":
                return status_data.get("rows", [])
            if status in ("FAILED", "CANCELLED"):
                log.warning("Hex: query %s run %s status: %s", query_id, run_id, status)
                return None
        except Exception as exc:
            log.warning("Hex: poll error for query %s: %s", query_id, exc)

        time.sleep(_POLL_INTERVAL)
        elapsed += _POLL_INTERVAL

    log.warning("Hex: query %s timed out after %ds", query_id, _MAX_POLL_SECONDS)
    return None


def get_chart_url(chart_id: str) -> str | None:
    """Return the embed URL for a Hex saved chart.

    Returns None if Hex is not configured or chart_id is empty.
    """
    token, workspace = _get_credentials()
    if not token or not workspace or not chart_id:
        return None

    return f"https://app.hex.tech/{workspace}/chart/{chart_id}"


def fetch_data_model(query_id: str) -> dict[str, Any] | None:
    """Fetch metadata about a Hex data model / saved query.

    Returns schema info (column names, types) or None.
    """
    token, workspace = _get_credentials()
    if not token or not workspace or not query_id:
        return None

    headers = {"Authorization": f"Bearer {token}"}
    url = f"{_HEX_API_BASE}/workspace/{workspace}/query/{query_id}"

    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        log.warning("Hex: failed to fetch data model %s: %s", query_id, exc)
        return None
