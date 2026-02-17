"""Email connector — send recap via Gmail SMTP."""

from __future__ import annotations

import logging
import os
import re
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

log = logging.getLogger(__name__)

# Slack emoji → Unicode/HTML replacements
_EMOJI_MAP = {
    ":newspaper:": "\U0001f4f0",
    ":rotating_light:": "\U0001f6a8",
    ":red_circle:": "\U0001f534",
    ":large_yellow_circle:": "\U0001f7e1",
    ":white_circle:": "\u26aa",
    ":link:": "\U0001f517",
    ":arrow_right:": "\u27a1\ufe0f",
    ":question:": "\u2753",
    ":eyes:": "\U0001f440",
}


def _slack_to_html(text: str) -> str:
    """Convert Slack-flavoured plain text to simple HTML."""
    html = text

    # Replace Slack emoji shortcodes with Unicode
    for code, char in _EMOJI_MAP.items():
        html = html.replace(code, char)

    # Convert Slack links <url|label> → HTML <a>
    html = re.sub(r"<([^|>]+)\|([^>]+)>", r'<a href="\1">\2</a>', html)

    # Convert *bold* → <strong> (but not inside HTML tags)
    html = re.sub(r"\*([^*\n]+)\*", r"<strong>\1</strong>", html)

    # Convert _italic_ → <em>
    html = re.sub(r"_([^_\n]+)_", r"<em>\1</em>", html)

    # Wrap in basic HTML structure with line breaks
    lines = html.split("\n")
    body_lines = []
    for line in lines:
        if line.startswith("=" * 10):
            body_lines.append("<hr>")
        else:
            body_lines.append(line + "<br>")

    return (
        "<!DOCTYPE html><html><head>"
        '<meta charset="utf-8">'
        "<style>"
        "body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', "
        "Roboto, sans-serif; line-height: 1.6; color: #333; max-width: 700px; "
        "margin: 0 auto; padding: 20px; }"
        "a { color: #1a73e8; }"
        "hr { border: none; border-top: 2px solid #e0e0e0; margin: 12px 0; }"
        "</style></head><body>\n"
        + "\n".join(body_lines)
        + "\n</body></html>"
    )


def _get_config() -> tuple[str, str, str]:
    """Return (sender_email, app_password, recipient_email)."""
    sender = os.environ.get("GMAIL_ADDRESS")
    password = os.environ.get("GMAIL_APP_PASSWORD")
    recipient = os.environ.get("RECAP_RECIPIENT_EMAIL")

    if not sender or not password:
        raise RuntimeError(
            "GMAIL_ADDRESS and GMAIL_APP_PASSWORD must be set. "
            "Generate an App Password at https://myaccount.google.com/apppasswords"
        )
    # Default recipient to sender (send to yourself)
    if not recipient:
        recipient = sender

    return sender, password, recipient


def send_recap(recap_text: str, *, subject: str | None = None) -> bool:
    """Send the daily recap via Gmail SMTP.

    Returns True on success, False otherwise.
    """
    sender, password, recipient = _get_config()

    if not subject:
        from datetime import datetime
        subject = f"Daily Strategy Recap — {datetime.utcnow().strftime('%Y-%m-%d')}"

    msg = MIMEMultipart("alternative")
    msg["From"] = sender
    msg["To"] = recipient
    msg["Subject"] = subject

    # Attach both plain text and HTML versions
    msg.attach(MIMEText(recap_text, "plain"))
    msg.attach(MIMEText(_slack_to_html(recap_text), "html"))

    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as server:
            server.starttls()
            server.login(sender, password)
            server.sendmail(sender, [recipient], msg.as_string())
        log.info("Recap email sent to %s", recipient)
        return True
    except smtplib.SMTPException as exc:
        log.error("Failed to send email: %s", exc)
        return False
