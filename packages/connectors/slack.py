"""Slack connector — post messages via incoming webhook."""

from __future__ import annotations

import json
import logging
import os

import requests

log = logging.getLogger(__name__)


def _get_webhook_url() -> str:
    url = os.environ.get("SLACK_WEBHOOK_URL")
    if not url:
        raise RuntimeError(
            "SLACK_WEBHOOK_URL env var is not set. "
            "Create an incoming webhook in your Slack workspace."
        )
    return url


def post_message(text: str, *, webhook_url: str | None = None) -> bool:
    """Post a plain-text message to Slack via an incoming webhook.

    Returns True on success, False otherwise.
    """
    url = webhook_url or _get_webhook_url()
    payload = {"text": text}

    try:
        resp = requests.post(
            url,
            data=json.dumps(payload),
            headers={"Content-Type": "application/json"},
            timeout=15,
        )
        if resp.status_code == 200 and resp.text == "ok":
            log.info("Slack message posted successfully")
            return True
        else:
            log.error(
                "Slack webhook returned %d: %s", resp.status_code, resp.text[:200]
            )
            return False
    except requests.RequestException as exc:
        log.error("Failed to post to Slack: %s", exc)
        return False
