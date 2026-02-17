"""Google service-account authentication."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from googleapiclient.discovery import Resource

log = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/documents.readonly",
]


def _get_credentials() -> Any:
    from google.oauth2 import service_account

    creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not creds_path:
        raise RuntimeError(
            "GOOGLE_APPLICATION_CREDENTIALS env var is not set. "
            "Point it to your service-account JSON key file."
        )
    creds = service_account.Credentials.from_service_account_file(
        creds_path, scopes=SCOPES
    )
    log.info("Authenticated as %s", creds.service_account_email)
    return creds


def get_drive_service() -> Resource:
    """Return an authorized Google Drive API v3 client."""
    from googleapiclient.discovery import build

    return build("drive", "v3", credentials=_get_credentials(), cache_discovery=False)


def get_docs_service() -> Resource:
    """Return an authorized Google Docs API v1 client."""
    from googleapiclient.discovery import build

    return build("docs", "v1", credentials=_get_credentials(), cache_discovery=False)
