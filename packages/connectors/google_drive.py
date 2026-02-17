"""Google Drive connector — list docs and comments from shared folders."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from googleapiclient.discovery import Resource

from packages.connectors.google_auth import get_drive_service

log = logging.getLogger(__name__)


def list_docs_in_folders(
    folder_ids: list[str],
    mime_types: list[str],
    max_files: int = 50,
    *,
    drive_service: Resource | None = None,
) -> list[dict[str, Any]]:
    """List Google Docs inside the given Drive folders.

    Returns a list of dicts with keys:
        file_id, name, mime_type, webViewLink, modifiedTime
    """
    service = drive_service or get_drive_service()

    # Build a query: file in any of the folders, matching mime types
    folder_clauses = " or ".join(f"'{fid}' in parents" for fid in folder_ids)
    mime_clauses = " or ".join(f"mimeType='{m}'" for m in mime_types)
    query = f"({folder_clauses}) and ({mime_clauses}) and trashed=false"

    files: list[dict[str, Any]] = []
    page_token: str | None = None

    while len(files) < max_files:
        resp = (
            service.files()
            .list(
                q=query,
                fields="nextPageToken, files(id, name, mimeType, webViewLink, modifiedTime)",
                pageSize=min(100, max_files - len(files)),
                pageToken=page_token,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            .execute()
        )

        for f in resp.get("files", []):
            files.append(
                {
                    "file_id": f["id"],
                    "name": f["name"],
                    "mime_type": f["mimeType"],
                    "webViewLink": f.get("webViewLink", ""),
                    "modifiedTime": f.get("modifiedTime", ""),
                }
            )

        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    log.info("Found %d docs across %d folders", len(files), len(folder_ids))
    return files[:max_files]


def list_comments(
    file_id: str,
    *,
    drive_service: Resource | None = None,
) -> list[dict[str, Any]]:
    """Fetch all comments (resolved and unresolved) for a Google Doc.

    Returns a list of dicts with keys:
        comment_id, status, author, updatedTime, content, quotedFileContent,
        webViewLink
    """
    service = drive_service or get_drive_service()

    comments: list[dict[str, Any]] = []
    page_token: str | None = None

    while True:
        resp = (
            service.comments()
            .list(
                fileId=file_id,
                fields=(
                    "nextPageToken, comments(id, resolved, author, "
                    "modifiedTime, content, quotedFileContent, htmlContent)"
                ),
                pageSize=100,
                includeDeleted=False,
                pageToken=page_token,
            )
            .execute()
        )

        for c in resp.get("comments", []):
            author_info = c.get("author", {})
            comments.append(
                {
                    "comment_id": c["id"],
                    "status": "resolved" if c.get("resolved") else "open",
                    "author": author_info.get("displayName", "Unknown"),
                    "updatedTime": c.get("modifiedTime", ""),
                    "content": c.get("content", ""),
                    "quotedFileContent": (
                        c.get("quotedFileContent", {}).get("value")
                        if c.get("quotedFileContent")
                        else None
                    ),
                    # Drive API does not return per-comment URLs; construct from file
                    "webViewLink": f"https://docs.google.com/document/d/{file_id}/edit?disco={c['id']}",
                }
            )

        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    log.info("File %s: fetched %d comments", file_id, len(comments))
    return comments
