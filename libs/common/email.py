"""
Email sending utilities using Brevo SMTP.
"""

import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
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


async def send_enrollment_confirmation_email(
    to_email: str,
    member_name: str,
    program_name: str,
    cohort_name: str,
    start_date: str,
) -> bool:
    """
    Send enrollment confirmation email to a member.
    """
    subject = f"Welcome to {program_name}! Your enrollment is confirmed."

    body = f"""Hi {member_name},

Congratulations! Your enrollment in the SwimBuddz Academy has been confirmed.

Program: {program_name}
Cohort: {cohort_name}
Start Date: {start_date}

What's Next:
- Sessions will appear in your Sessions page once they're scheduled
- Make sure your profile is complete with emergency contact information
- Review the program curriculum on your Academy dashboard

If you have any questions, please reach out to our team.

See you in the water!

— The SwimBuddz Team
"""

    html_body = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #333; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background: linear-gradient(135deg, #0891b2 0%, #0284c7 100%); color: white; padding: 30px; border-radius: 12px 12px 0 0; }}
        .content {{ background: #f8fafc; padding: 30px; border-radius: 0 0 12px 12px; }}
        .details {{ background: white; padding: 20px; border-radius: 8px; margin: 20px 0; border-left: 4px solid #0891b2; }}
        .details p {{ margin: 8px 0; }}
        .label {{ color: #64748b; font-size: 14px; }}
        .value {{ font-weight: 600; color: #1e293b; }}
        .footer {{ text-align: center; color: #64748b; font-size: 14px; margin-top: 20px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1 style="margin: 0;">🏊‍♂️ Welcome to {program_name}!</h1>
            <p style="margin: 10px 0 0 0; opacity: 0.9;">Your enrollment has been confirmed</p>
        </div>
        <div class="content">
            <p>Hi {member_name},</p>
            <p>Congratulations! Your enrollment in the SwimBuddz Academy has been confirmed.</p>
            
            <div class="details">
                <p><span class="label">Program:</span> <span class="value">{program_name}</span></p>
                <p><span class="label">Cohort:</span> <span class="value">{cohort_name}</span></p>
                <p><span class="label">Start Date:</span> <span class="value">{start_date}</span></p>
            </div>
            
            <h3>What's Next:</h3>
            <ul>
                <li>Sessions will appear in your Sessions page once they're scheduled</li>
                <li>Make sure your profile is complete with emergency contact information</li>
                <li>Review the program curriculum on your Academy dashboard</li>
            </ul>
            
            <p>If you have any questions, please reach out to our team.</p>
            
            <p>See you in the water! 🏊‍♂️</p>
            
            <div class="footer">
                <p>— The SwimBuddz Team</p>
            </div>
        </div>
    </div>
</body>
</html>
"""

    return await send_email(to_email, subject, body, html_body)


async def send_payment_approved_email(
    to_email: str,
    payment_reference: str,
    purpose: str,
    amount: float,
    currency: str = "NGN",
) -> bool:
    """
    Send payment approval notification to a member.
    """
    purpose_display = purpose.replace("_", " ").title()
    amount_display = (
        f"₦{amount:,.0f}" if currency == "NGN" else f"{currency} {amount:,.2f}"
    )

    subject = f"Payment Approved - {purpose_display}"

    body = f"""Hi there,

Great news! Your manual payment has been verified and approved.

Payment Details:
- Reference: {payment_reference}
- Purpose: {purpose_display}
- Amount: {amount_display}

Your membership/enrollment has been activated. You can now access all associated features.

Thank you for being part of SwimBuddz!

— The SwimBuddz Team
"""

    html_body = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #333; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background: linear-gradient(135deg, #10b981 0%, #059669 100%); color: white; padding: 30px; border-radius: 12px 12px 0 0; }}
        .content {{ background: #f8fafc; padding: 30px; border-radius: 0 0 12px 12px; }}
        .details {{ background: white; padding: 20px; border-radius: 8px; margin: 20px 0; border-left: 4px solid #10b981; }}
        .details p {{ margin: 8px 0; }}
        .label {{ color: #64748b; font-size: 14px; }}
        .value {{ font-weight: 600; color: #1e293b; }}
        .footer {{ text-align: center; color: #64748b; font-size: 14px; margin-top: 20px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1 style="margin: 0;">✅ Payment Approved!</h1>
            <p style="margin: 10px 0 0 0; opacity: 0.9;">Your manual payment has been verified</p>
        </div>
        <div class="content">
            <p>Hi there,</p>
            <p>Great news! Your manual payment has been verified and approved.</p>
            
            <div class="details">
                <p><span class="label">Reference:</span> <span class="value">{payment_reference}</span></p>
                <p><span class="label">Purpose:</span> <span class="value">{purpose_display}</span></p>
                <p><span class="label">Amount:</span> <span class="value">{amount_display}</span></p>
            </div>
            
            <p>Your membership/enrollment has been activated. You can now access all associated features.</p>
            
            <p>Thank you for being part of SwimBuddz! 🏊‍♂️</p>
            
            <div class="footer">
                <p>— The SwimBuddz Team</p>
            </div>
        </div>
    </div>
</body>
</html>
"""

    return await send_email(to_email, subject, body, html_body)
