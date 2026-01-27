"""
Core email sending utilities using Brevo SMTP.
"""

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from libs.common.config import get_settings
from libs.common.logging import get_logger

logger = get_logger(__name__)


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
    Send an email using Brevo SMTP.

    Args:
        to_email: Recipient email address
        subject: Email subject line
        body: Plain text body
        html_body: Optional HTML body (if not provided, plain text is used)
        from_email: Sender email (defaults to no-reply@swimbuddz.com)
        from_name: Sender name (defaults to SwimBuddz)

    Returns:
        True if email was sent successfully, False otherwise
    """
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

    sender_email = from_email or settings.DEFAULT_FROM_EMAIL
    sender_name = from_name or settings.DEFAULT_FROM_NAME

    try:
        # Create message
        if html_body:
            msg = MIMEMultipart("alternative")
            msg.attach(MIMEText(body, "plain"))
            msg.attach(MIMEText(html_body, "html"))
        else:
            msg = MIMEText(body, "plain")

        msg["Subject"] = subject
        msg["From"] = f"{sender_name} <{sender_email}>"
        msg["To"] = to_email

        logger.info(f"Sending email to {to_email}: {subject}")

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
