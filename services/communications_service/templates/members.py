"""
Member-specific email templates.

These templates handle notifications for member lifecycle events:
- Membership approval
- Membership rejection
- Password reset
"""

from libs.common.emails.core import send_email
from services.communications_service.templates.base import (
    GRADIENT_AMBER,
    GRADIENT_CYAN,
    GRADIENT_GREEN,
    cta_button,
    detail_box,
    info_box,
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


async def send_password_reset_email(
    to_email: str,
    reset_url: str,
) -> bool:
    """
    Send branded password reset email with a one-time reset link.

    Args:
        to_email: Recipient email address.
        reset_url: The Supabase-generated signed reset URL (redirects to /reset-password).
    """
    subject = "Reset your SwimBuddz password"

    body = (
        "Hi,\n\n"
        "We received a request to reset the password for your SwimBuddz account.\n\n"
        f"Reset your password here: {reset_url}\n\n"
        "This link expires in 1 hour. If you didn't request a password reset, "
        "you can safely ignore this email — your password won't change.\n\n"
        "The SwimBuddz Team"
    )

    body_html = (
        "<p>We received a request to reset the password for your SwimBuddz account.</p>"
        + cta_button("Reset password", reset_url, color="#0891b2")
        + info_box(
            content=(
                "⏱ This link expires in <strong>1 hour</strong>. "
                "If you didn't request a password reset, you can safely ignore "
                "this email — your password won't change."
            ),
            bg_color="#f0f9ff",
            border_color="#0891b2",
        )
        + '<hr class="divider" />'
        + '<p style="font-size: 13px; color: #94a3b8;">Button not working? Copy and paste this link into your browser:<br/>'
        + f'<a href="{reset_url}" style="color: #0891b2; word-break: break-all;">{reset_url}</a></p>'
    )

    html_body = wrap_html(
        title="Reset your password",
        subtitle="Follow the link below to choose a new password",
        body_html=body_html,
        header_gradient=GRADIENT_CYAN,
        preheader="Reset your SwimBuddz password — link expires in 1 hour",
    )

    return await send_email(to_email, subject, body, html_body)
