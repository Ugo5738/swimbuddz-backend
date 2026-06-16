"""
Core email sending utilities.

Primary transport is Brevo's HTTP API (api.brevo.com:443). Cloud hosts such as
DigitalOcean block outbound SMTP ports (25/465/587), which makes smtplib
delivery time out — so when a Brevo v3 API key (``BREVO_API_KEY``, an
``xkeysib-`` key) is configured we send over HTTPS. Without it we fall back to
SMTP for environments where the ports are open (local/dev).
"""

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

import httpx
from libs.common.config import get_settings
from libs.common.logging import get_logger

logger = get_logger(__name__)

BREVO_API_URL = "https://api.brevo.com/v3/smtp/email"


def _get_smtp_password() -> str:
    """Get SMTP password from settings (BREVO_KEY takes priority over SMTP_PASSWORD)."""
    settings = get_settings()
    return settings.BREVO_KEY or settings.SMTP_PASSWORD


async def send_email(
    to_email: str,
    subject: str,
    body: str,
    html_body: Optional[str] = None,
    from_email: Optional[str] = None,
    from_name: Optional[str] = None,
) -> bool:
    """
    Send a single email. Uses the Brevo HTTP API when ``BREVO_API_KEY`` is set
    (required on hosts that block SMTP), otherwise direct SMTP.

    Returns:
        True if the email was accepted for delivery, False otherwise.
    """
    settings = get_settings()
    sender_email = from_email or settings.DEFAULT_FROM_EMAIL
    sender_name = from_name or settings.DEFAULT_FROM_NAME

    api_key = getattr(settings, "BREVO_API_KEY", "") or ""
    if api_key:
        return await _send_via_brevo_api(
            api_key, to_email, subject, body, html_body, sender_email, sender_name
        )
    return await _send_via_smtp(
        to_email, subject, body, html_body, sender_email, sender_name
    )


async def _send_via_brevo_api(
    api_key: str,
    to_email: str,
    subject: str,
    body: str,
    html_body: Optional[str],
    sender_email: str,
    sender_name: str,
) -> bool:
    """Send via Brevo's transactional email HTTP API (works over port 443)."""
    payload: dict = {
        "sender": {"email": sender_email, "name": sender_name},
        "to": [{"email": to_email}],
        "subject": subject,
        "textContent": body,
    }
    if html_body:
        payload["htmlContent"] = html_body

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                BREVO_API_URL,
                headers={
                    "api-key": api_key,
                    "content-type": "application/json",
                    "accept": "application/json",
                },
                json=payload,
            )
        # Brevo returns 201 Created (with a messageId) on success.
        if resp.status_code in (200, 201):
            logger.info(f"Email sent to {to_email} via Brevo API: {subject}")
            return True
        logger.error(
            f"Brevo API send to {to_email} failed: {resp.status_code} {resp.text[:300]}"
        )
        return False
    except Exception as e:
        logger.error(
            f"Brevo API request error sending to {to_email}: {type(e).__name__}: {e}"
        )
        return False


async def _send_via_smtp(
    to_email: str,
    subject: str,
    body: str,
    html_body: Optional[str],
    sender_email: str,
    sender_name: str,
) -> bool:
    """Send via Brevo SMTP. Blocked on hosts that close outbound SMTP ports."""
    settings = get_settings()
    smtp_password = _get_smtp_password()

    if not smtp_password or not settings.SMTP_USERNAME:
        if not smtp_password:
            logger.warning("BREVO_KEY/SMTP_PASSWORD not configured - email not sent")
        if not settings.SMTP_USERNAME:
            logger.warning("SMTP_USERNAME not configured - email not sent")
        logger.info(f"Would have sent email to {to_email}: {subject}")
        logger.debug(f"Email body: {body[:200]}...")
        return False

    try:
        if html_body:
            msg = MIMEMultipart("alternative")
            msg.attach(MIMEText(body, "plain"))
            msg.attach(MIMEText(html_body, "html"))
        else:
            msg = MIMEText(body, "plain")

        msg["Subject"] = subject
        msg["From"] = f"{sender_name} <{sender_email}>"
        msg["To"] = to_email

        logger.info(f"Sending email to {to_email} via SMTP: {subject}")

        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=30) as server:
            server.starttls()
            server.login(settings.SMTP_USERNAME, smtp_password)
            server.sendmail(sender_email, to_email, msg.as_string())

        logger.info(f"Email sent successfully to {to_email}")
        return True

    except smtplib.SMTPAuthenticationError as e:
        logger.error(f"SMTP authentication failed: {e}")
        return False
    except smtplib.SMTPException as e:
        logger.error(f"SMTP error sending email: {e}")
        return False
    except Exception as e:
        logger.error(f"Failed to send email: {type(e).__name__}: {e}")
        return False
