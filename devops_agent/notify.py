"""Fastmail SMTP email sender.

Uses Python stdlib smtplib — no extra dependencies.
Fail-fast on missing env vars.
"""

import logging
import os
import smtplib
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from .errors import ErrorCode, NotifyError
from .models import NotifyResult

logger = logging.getLogger(__name__)

FASTMAIL_SMTP = "smtp.fastmail.com"
FASTMAIL_PORT = 465  # SSL


def _get_smtp_config() -> tuple[str, str, str]:
    """Load SMTP config from env vars. Fail-fast."""
    password = os.environ.get("FASTMAIL_APP_PASSWORD")
    if not password:
        raise NotifyError("FASTMAIL_APP_PASSWORD env var not set")

    sender = os.environ.get("FASTMAIL_FROM")
    if not sender:
        raise NotifyError("FASTMAIL_FROM env var not set")

    default_to = os.environ.get("DEVOPS_NOTIFY_TO", sender)
    return password, sender, default_to


def send_email(
    *,
    to: str | None = None,
    subject: str,
    body_text: str,
    body_html: str | None = None,
) -> NotifyResult:
    """Send an email via Fastmail SMTP.

    Args:
        to: Recipient address. Falls back to DEVOPS_NOTIFY_TO or FASTMAIL_FROM.
        subject: Email subject line.
        body_text: Plain text body (required).
        body_html: Optional HTML body.

    Returns:
        NotifyResult with message_id on success.
    """
    t0 = time.monotonic()

    try:
        password, sender, default_to = _get_smtp_config()
    except NotifyError as e:
        return NotifyResult(
            ok=False, code=ErrorCode.CONFIG_ERROR, message=str(e)
        )

    recipient = to or default_to

    msg = MIMEMultipart("alternative")
    msg["From"] = sender
    msg["To"] = recipient
    msg["Subject"] = subject
    msg.attach(MIMEText(body_text, "plain"))
    if body_html:
        msg.attach(MIMEText(body_html, "html"))

    try:
        with smtplib.SMTP_SSL(FASTMAIL_SMTP, FASTMAIL_PORT, timeout=15) as server:
            server.login(sender, password)
            server.send_message(msg)
            message_id = msg.get("Message-ID", "")
    except smtplib.SMTPAuthenticationError as e:
        return NotifyResult(
            ok=False,
            code=ErrorCode.AUTH_ERROR,
            message=f"SMTP auth failed: {e}",
            recipient=recipient,
        )
    except (smtplib.SMTPException, OSError) as e:
        return NotifyResult(
            ok=False,
            code=ErrorCode.SMTP_ERROR,
            message=f"SMTP error: {e}",
            recipient=recipient,
        )

    latency_ms = (time.monotonic() - t0) * 1000
    logger.info(
        "send_email to=%s subject=%s latency_ms=%.0f accepted=true",
        recipient,
        subject,
        latency_ms,
    )

    return NotifyResult(
        ok=True,
        code=ErrorCode.OK,
        message=f"Email accepted by SMTP server for {recipient}",
        message_id=message_id,
        recipient=recipient,
    )
