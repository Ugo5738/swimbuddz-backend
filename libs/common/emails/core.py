"""
Core email sending utilities using Brevo SMTP.
"""

import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from libs.common.logging import get_logger

logger = get_logger(__name__)

# Brevo SMTP settings
SMTP_HOST = "smtp-relay.brevo.com"
SMTP_PORT = 587
SMTP_USERNAME = "9e85f2001@smtp-brevo.com"
DEFAULT_FROM_EMAIL = "no-reply@swimbuddz.com"
DEFAULT_FROM_NAME = "SwimBuddz"


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
    smtp_password = os.environ.get("BREVO_KEY")

    if not smtp_password:
        logger.warning("BREVO_KEY not found in environment - email not sent")
        logger.info(f"Would have sent email to {to_email}: {subject}")
        logger.debug(f"Email body: {body[:200]}...")
        return False

    sender_email = from_email or DEFAULT_FROM_EMAIL
    sender_name = from_name or DEFAULT_FROM_NAME

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

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
            server.starttls()
            server.login(SMTP_USERNAME, smtp_password)
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
