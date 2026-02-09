"""
Member-specific email templates.

These templates handle notifications for member lifecycle events:
- Membership approval
- Membership rejection
"""

from libs.common.emails.core import send_email
from services.communications_service.templates.base import (
    GRADIENT_AMBER,
    GRADIENT_GREEN,
    cta_button,
    detail_box,
    wrap_html,
)


async def send_member_approved_email(
    to_email: str,
    member_name: str,
    login_url: str = "https://swimbuddz.com/auth/login",
) -> bool:
    """
    Send branded email when a member application is approved.
    """
    subject = "Welcome to SwimBuddz! Your account is approved"

    body = (
        f"Hi {member_name},\n\n"
        "Congratulations! Your SwimBuddz membership application has been "
        "approved.\n\n"
        "You can now log in and access all member features.\n\n"
        f"Log in: {login_url}\n\n"
        "Welcome to the community!\n"
        "The SwimBuddz Team"
    )

    body_html = (
        f"<p>Hi {member_name},</p>"
        "<p>Congratulations! Your SwimBuddz membership application has been approved.</p>"
        "<p>You can now log in and access all member features.</p>"
        + cta_button("Log In to SwimBuddz", login_url, color="#10b981")
        + "<p>Welcome to the community!</p>"
    )

    html_body = wrap_html(
        title="Welcome to SwimBuddz!",
        subtitle="Your membership has been approved",
        body_html=body_html,
        header_gradient=GRADIENT_GREEN,
        preheader="Your SwimBuddz membership has been approved",
    )

    return await send_email(to_email, subject, body, html_body)


async def send_member_rejected_email(
    to_email: str,
    member_name: str,
    rejection_reason: str = "Does not meet current criteria",
) -> bool:
    """
    Send branded email when a member application is rejected.
    """
    subject = "Update on your SwimBuddz application"

    body = (
        f"Hi {member_name},\n\n"
        "Thank you for your interest in SwimBuddz.\n\n"
        "After reviewing your application, we are unable to approve your "
        "membership at this time.\n\n"
        f"Reason: {rejection_reason}\n\n"
        "You are welcome to reapply in the future.\n\n"
        "Best regards,\n"
        "The SwimBuddz Team"
    )

    body_html = (
        f"<p>Hi {member_name},</p>"
        "<p>Thank you for your interest in SwimBuddz.</p>"
        "<p>After reviewing your application, we are unable to approve your "
        "membership at this time.</p>"
        + detail_box({"Reason": rejection_reason}, accent_color="#d97706")
        + "<p>You are welcome to reapply in the future.</p>"
    )

    html_body = wrap_html(
        title="Application Update",
        subtitle="Thank you for your interest in SwimBuddz",
        body_html=body_html,
        header_gradient=GRADIENT_AMBER,
        preheader="Update on your SwimBuddz membership application",
    )

    return await send_email(to_email, subject, body, html_body)
