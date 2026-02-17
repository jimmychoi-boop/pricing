"""Google Docs connector — fetch and parse document content."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from googleapiclient.discovery import Resource

from packages.connectors.google_auth import get_docs_service

log = logging.getLogger(__name__)


def _extract_text_from_elements(elements: list[dict]) -> str:
    """Recursively extract plain text from Docs structural elements."""
    parts: list[str] = []
    for elem in elements:
        if "paragraph" in elem:
            for run in elem["paragraph"].get("elements", []):
                text_run = run.get("textRun")
                if text_run:
                    parts.append(text_run.get("content", ""))
        elif "table" in elem:
            for row in elem["table"].get("tableRows", []):
                for cell in row.get("tableCells", []):
                    parts.append(_extract_text_from_elements(cell.get("content", [])))
        elif "sectionBreak" in elem:
            pass  # skip
    return "".join(parts)


def _heading_prefix(paragraph: dict) -> str:
    """Return a Markdown-style heading prefix if the paragraph has a heading style."""
    style = paragraph.get("paragraphStyle", {}).get("namedStyleType", "")
    mapping = {
        "HEADING_1": "# ",
        "HEADING_2": "## ",
        "HEADING_3": "### ",
        "HEADING_4": "#### ",
        "HEADING_5": "##### ",
        "HEADING_6": "###### ",
    }
    return mapping.get(style, "")


def fetch_doc_plain_text(
    file_id: str,
    *,
    docs_service: Resource | None = None,
    heading_aware: bool = True,
) -> str:
    """Fetch a Google Doc and return its content as plain text.

    If heading_aware=True, headings are prefixed with Markdown-style '#' markers.
    """
    service = docs_service or get_docs_service()
    doc = service.documents().get(documentId=file_id).execute()
    body = doc.get("body", {})
    elements = body.get("content", [])

    parts: list[str] = []
    for elem in elements:
        if "paragraph" in elem:
            prefix = ""
            if heading_aware:
                prefix = _heading_prefix(elem["paragraph"])
            for run in elem["paragraph"].get("elements", []):
                text_run = run.get("textRun")
                if text_run:
                    content = text_run.get("content", "")
                    if prefix and content.strip():
                        parts.append(prefix + content)
                    else:
                        parts.append(content)
        elif "table" in elem:
            parts.append(_extract_text_from_elements([elem]))

    text = "".join(parts)
    log.info("Fetched doc %s: %d chars", file_id, len(text))
    return text
