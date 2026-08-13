"""Branded emails for private session media-vault access."""

from html import escape

from libs.common.emails.core import send_email
from services.communications_service.templates.base import (
    GRADIENT_PURPLE,
    cta_button,
    detail_box,
    info_box,
    sign_off,
    wrap_html,
)


async def send_media_vault_access_email(
    *,
    to_email: str,
    member_name: str,
    vault_title: str,
    role_label: str,
    responsibility: str,
    expires_at: str,
    action_url: str,
) -> bool:
    """Tell an assigned volunteer how to use their time-limited vault access."""

    safe_name = escape(member_name or "SwimBuddz member")
    safe_title = escape(vault_title)
    safe_role = escape(role_label)
    safe_responsibility = escape(responsibility)
    safe_expiry = escape(expires_at)
    safe_action_url = escape(action_url, quote=True)
    subject = f"Media vault assignment: {vault_title}"
    body = f"""Hi {member_name or 'SwimBuddz member'},

You have been assigned as the {role_label} for {vault_title}.

Your responsibility: {responsibility}.
Access ends: {expires_at}.

Open the media vault: {action_url}

Please keep original session media inside the private vault until it has been reviewed and published.

— The SwimBuddz Team
"""
    body_html = (
        f"<p>Hi {safe_name},</p>"
        f"<p>You have been assigned as the <strong>{safe_role}</strong> for "
        f"<strong>{safe_title}</strong>.</p>"
        + detail_box(
            {
                "Assignment": safe_role,
                "What you can do": safe_responsibility,
                "Access ends": safe_expiry,
            },
            accent_color="#8b5cf6",
        )
        + cta_button("Open the media vault", safe_action_url, color="#7c3aed")
        + info_box(
            "Keep original session media inside the private vault until it has been reviewed and published.",
            bg_color="#f5f3ff",
            border_color="#8b5cf6",
            title="Private workspace",
        )
        + sign_off("Thank you for helping capture the SwimBuddz experience.")
    )
    html_body = wrap_html(
        title="Media vault access",
        subtitle=safe_title,
        body_html=body_html,
        header_gradient=GRADIENT_PURPLE,
        preheader=f"Your {safe_role} access for {safe_title}",
    )
    return await send_email(to_email, subject, body, html_body)
