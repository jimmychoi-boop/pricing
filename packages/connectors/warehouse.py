"""Snowflake warehouse connector.

Executes SQL queries against a Snowflake data warehouse and returns results
as Python dicts. Used by the metrics connector to resolve metric values
when SNOWFLAKE_ACCOUNT is configured.

Connection credentials are read from environment variables:
  SNOWFLAKE_ACCOUNT   — e.g. "xy12345.us-east-1"
  SNOWFLAKE_USER      — service account username
  SNOWFLAKE_PASSWORD   — service account password
  SNOWFLAKE_WAREHOUSE  — compute warehouse (e.g. "ANALYTICS_WH")
  SNOWFLAKE_DATABASE   — database name (e.g. "ANALYTICS")
  SNOWFLAKE_SCHEMA     — schema name (e.g. "PROD")
  SNOWFLAKE_ROLE       — optional role to assume
"""

from __future__ import annotations

import logging
import os
from typing import Any

log = logging.getLogger(__name__)

_conn_cache: Any = None


def is_available() -> bool:
    """Return True if Snowflake credentials are configured."""
    return bool(
        os.getenv("SNOWFLAKE_ACCOUNT")
        and os.getenv("SNOWFLAKE_USER")
        and os.getenv("SNOWFLAKE_PASSWORD")
    )


def _get_connection():
    """Return a cached Snowflake connection, creating one if needed."""
    global _conn_cache

    if _conn_cache is not None:
        try:
            _conn_cache.cursor().execute("SELECT 1")
            return _conn_cache
        except Exception:
            _conn_cache = None

    try:
        import snowflake.connector
    except ImportError:
        log.error(
            "snowflake-connector-python is not installed. "
            "Run: pip install snowflake-connector-python"
        )
        return None

    connect_params = {
        "account": os.getenv("SNOWFLAKE_ACCOUNT"),
        "user": os.getenv("SNOWFLAKE_USER"),
        "password": os.getenv("SNOWFLAKE_PASSWORD"),
    }

    warehouse = os.getenv("SNOWFLAKE_WAREHOUSE")
    if warehouse:
        connect_params["warehouse"] = warehouse

    database = os.getenv("SNOWFLAKE_DATABASE")
    if database:
        connect_params["database"] = database

    schema = os.getenv("SNOWFLAKE_SCHEMA")
    if schema:
        connect_params["schema"] = schema

    role = os.getenv("SNOWFLAKE_ROLE")
    if role:
        connect_params["role"] = role

    try:
        _conn_cache = snowflake.connector.connect(**connect_params)
        log.info(
            "Connected to Snowflake: %s/%s.%s",
            connect_params["account"],
            database or "(default)",
            schema or "(default)",
        )
        return _conn_cache
    except Exception as exc:
        log.error("Failed to connect to Snowflake: %s", exc)
        return None


def execute_query(sql: str) -> list[dict[str, Any]]:
    """Execute a SQL query and return results as a list of dicts.

    Returns an empty list if the connection fails or the query errors.
    """
    conn = _get_connection()
    if conn is None:
        return []

    try:
        cursor = conn.cursor()
        cursor.execute(sql)
        columns = [desc[0].lower() for desc in cursor.description]
        rows = cursor.fetchall()
        return [dict(zip(columns, row)) for row in rows]
    except Exception as exc:
        log.error("Snowflake query failed: %s\nSQL: %s", exc, sql[:200])
        return []
    finally:
        try:
            cursor.close()
        except Exception:
            pass


def execute_scalar(sql: str) -> float | int | None:
    """Execute a SQL query that returns a single scalar value.

    Expects the query to return one row with one column.
    Returns None if the query fails or returns no rows.
    """
    rows = execute_query(sql)
    if not rows:
        return None
    first_row = rows[0]
    # Return the first column value
    first_key = next(iter(first_row))
    val = first_row[first_key]
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None


def execute_segment_query(sql: str) -> dict[str, float]:
    """Execute a SQL query that returns segment breakdowns.

    Expects the query to return rows with two columns:
      (segment_name, value)

    Returns a dict mapping segment_name -> value.
    """
    rows = execute_query(sql)
    if not rows:
        return {}
    result: dict[str, float] = {}
    for row in rows:
        keys = list(row.keys())
        if len(keys) >= 2:
            seg_name = str(row[keys[0]])
            try:
                seg_value = float(row[keys[1]])
            except (TypeError, ValueError):
                continue
            result[seg_name] = seg_value
    return result


def close():
    """Close the cached Snowflake connection."""
    global _conn_cache
    if _conn_cache is not None:
        try:
            _conn_cache.close()
        except Exception:
            pass
        _conn_cache = None
