"""Public Stroke Lab analyzer email templates (guest ready / failed notices).

Guests have no member profile, so these take just an email + a link. They use
the shared branded layout (wrap_html) like every other transactional template.
"""

from libs.common.emails.core import send_email
from services.communications_service.templates.base import (
    GRADIENT_AMBER,
    GRADIENT_CYAN,
    cta_button,
    sign_off,
    wrap_html,
)


async def send_analyzer_ready_email(
    to_email: str,
    result_url: str,
    member_name: str = "",
) -> bool:
    """Notify a guest that their freestyle analysis is ready to view."""
    name_line = f"Hi {member_name}," if member_name else "Hi there,"
    greeting_html = (
        f"<p>Hi <strong>{member_name}</strong>,</p>"
        if member_name
        else "<p>Hi there,</p>"
    )
    subject = "Your SwimBuddz freestyle analysis is ready"

    body = (
        f"{name_line}\n\n"
        "Your freestyle stroke analysis is ready.\n\n"
        f"View it here: {result_url}\n\n"
        "— SwimBuddz Stroke Lab"
    )

    inner_html = (
        greeting_html
        + "<p>Your <strong>freestyle stroke analysis</strong> is ready — we measured "
        "your stroke rate, body roll, and breathing balance, flagged what to work "
        "on, and suggested drills.</p>"
        + cta_button("View My Analysis", result_url)
        + "<p style='color:#64748b;font-size:13px;margin-top:18px'>SwimBuddz Stroke "
        "Lab is an automated freestyle measurement tool — honest numbers, not a "
        "human coach.</p>" + sign_off()
    )

    html_body = wrap_html(
        title="Your analysis is ready",
        subtitle="Freestyle stroke breakdown",
        body_html=inner_html,
        header_gradient=GRADIENT_CYAN,
        preheader="Your freestyle stroke analysis is ready to view.",
    )

    return await send_email(to_email, subject, body, html_body)


async def send_analyzer_failed_email(
    to_email: str,
    retry_url: str = "https://analyzer.swimbuddz.com",
    member_name: str = "",
) -> bool:
    """Notify a guest that their clip couldn't be analyzed (credit refunded)."""
    name_line = f"Hi {member_name}," if member_name else "Hi there,"
    greeting_html = (
        f"<p>Hi <strong>{member_name}</strong>,</p>"
        if member_name
        else "<p>Hi there,</p>"
    )
    subject = "We couldn't analyze your freestyle clip"

    body = (
        f"{name_line}\n\n"
        "We couldn't analyze your clip this time, and your credit has been "
        "refunded.\n\n"
        "Tips: film side-on with the swimmer clearly in frame, 10–90 seconds, "
        "exported as MP4.\n\n"
        f"Try again: {retry_url}\n\n"
        "— SwimBuddz Stroke Lab"
    )

    inner_html = (
        greeting_html
        + "<p>We couldn't analyze your clip this time — and your credit has been "
        "<strong>refunded</strong>.</p>"
        + "<p>For the best results, film <strong>side-on</strong> with the swimmer "
        "clearly in frame, 10–90 seconds long, and exported as MP4.</p>"
        + cta_button("Try Another Clip", retry_url)
        + sign_off()
    )

    html_body = wrap_html(
        title="We couldn't analyze your clip",
        subtitle="Your credit has been refunded",
        body_html=inner_html,
        header_gradient=GRADIENT_AMBER,
        preheader="We couldn't analyze your clip — your credit was refunded.",
    )

    return await send_email(to_email, subject, body, html_body)
