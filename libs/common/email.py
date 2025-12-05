import logging

logger = logging.getLogger(__name__)


async def send_email(to_email: str, subject: str, body: str):
    """
    Mock email sender.
    In production, this would use SMTP or an email service provider (SendGrid, AWS SES, etc.).
    """
    logger.info("========== MOCK EMAIL ==========")
    logger.info(f"To: {to_email}")
    logger.info(f"Subject: {subject}")
    logger.info(f"Body: {body}")
    logger.info("================================")

    # Simulate success
    return True
