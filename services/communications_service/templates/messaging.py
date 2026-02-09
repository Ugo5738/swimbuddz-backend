"""
Generic messaging email templates.

These templates handle branded wrapping for user-composed messages:
- Coach-to-student/cohort messages
- Announcement emails
"""

from libs.common.emails.core import send_email
from services.communications_service.templates.base import (
    GRADIENT_CYAN,
    wrap_html,
)


async def send_message_email(
    to_email: str,
    subject: str,
    body: str,
) -> bool:
    """
    Send a user-composed message wrapped in the branded SwimBuddz template.

    Used for coach-to-student messages, cohort messages, and announcements.
    The body is treated as plain text and converted to HTML paragraphs.
    """
    body_html = body.replace("\n", "<br>")
    body_html = f"<div>{body_html}</div>"

    html_body = wrap_html(
        title=subject,
        body_html=body_html,
        header_gradient=GRADIENT_CYAN,
    )

    return await send_email(to_email, subject, body, html_body)
