"""Birthday-related email templates.

Sent by the daily birthday cron in services.communications_service.tasks.birthdays.
"""

from libs.common.emails.core import send_email
from services.communications_service.templates.base import (
    GRADIENT_CYAN,
    cta_button,
    info_box,
    sign_off,
    wrap_html,
)


async def send_birthday_email(
    to_email: str,
    member_name: str,
    dashboard_url: str = "https://swimbuddz.com/account",
) -> bool:
    """Send a birthday card email to an adult member."""
    subject = f"Happy birthday, {member_name}!"

    body = (
        f"Hi {member_name},\n\n"
        "Happy birthday from everyone at SwimBuddz!\n\n"
        "We hope your year ahead is full of strong strokes, calm waters, "
        "and plenty of moments worth celebrating — in and out of the pool.\n\n"
        "If you fancy marking the occasion with a swim, we'd love to see you "
        "at the next session.\n\n"
        f"Visit your dashboard: {dashboard_url}\n\n"
        "See you in the water,\n"
        "The SwimBuddz Team"
    )

    body_html = (
        f"<p>Hi {member_name},</p>"
        "<p><strong>Happy birthday from everyone at SwimBuddz!</strong></p>"
        + info_box(
            content=(
                "We hope your year ahead is full of strong strokes, calm waters, "
                "and plenty of moments worth celebrating — in and out of the pool."
            ),
            bg_color="#f0f9ff",
            border_color="#0891b2",
        )
        + "<p>If you fancy marking the occasion with a swim, we'd love to see you "
        "at the next session.</p>"
        + cta_button("Find a swim", dashboard_url, color="#0891b2")
        + sign_off("See you in the water!")
    )

    html_body = wrap_html(
        title=f"Happy birthday, {member_name}!",
        subtitle="A little something from the SwimBuddz family",
        body_html=body_html,
        header_gradient=GRADIENT_CYAN,
        preheader=f"Happy birthday from SwimBuddz, {member_name}!",
    )

    return await send_email(to_email, subject, body, html_body)
