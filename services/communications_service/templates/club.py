"""Branded Club assessment results; internal coach notes never enter this template."""

from datetime import date
from html import escape
from urllib.parse import urlencode

from libs.common.config import get_settings
from libs.common.emails.core import send_email
from services.communications_service.templates.base import (
    cta_button,
    detail_box,
    sign_off,
    wrap_html,
)


async def send_club_assessment_result_email(
    to_email: str,
    member_name: str,
    club_name: str,
    application_id: str,
    outcome: str,
    approved_payment_modes: list[str],
    transition_expires_at: str | None = None,
    primary_technique_focus: str | None = None,
    first_club_milestone: str | None = None,
) -> bool:
    base_url = get_settings().FRONTEND_URL.rstrip("/")
    if outcome == "academy_first":
        title = "Your SwimBuddz assessment is complete"
        message = (
            "We recommend starting with the SwimBuddz Academy before joining Club. "
            "The Academy gives you structured support to strengthen the skills "
            "you need for independent Club practice."
        )
        next_step = "View the available Academy programmes and choose a cohort that works for you."
        cta, url = "View Academy programmes", f"{base_url}/upgrade/academy/cohort"
    elif outcome in {"club_ready", "club_ready_modified"}:
        title = "You're approved for SwimBuddz Club!"
        message = (
            "You are Club-ready — with coaching focus. Your coach will help you "
            "work towards the goals below during Club practice."
            if outcome == "club_ready_modified"
            else "You are Club-ready. You can now complete your Club registration."
        )
        modes = list(dict.fromkeys(approved_payment_modes))
        if modes == ["transition_per_session"]:
            expiry = date.fromisoformat(transition_expires_at).strftime("%d %B %Y")
            next_step = (
                f"Pay per swim until {expiry}, with no quarterly Club fee upfront. "
                "You'll pay the price shown for each swim when you book. "
                "Review any annual SwimBuddz Membership due and your optional "
                "Community Experience before activating your Club access."
            )
        else:
            next_step = "Review how to join Club, any Membership due and your optional Community Experience."
        cta = "Continue Club registration"
        url = (
            f"{base_url}/checkout?"
            + urlencode(
                {
                    "purpose": "club",
                    "application_id": application_id,
                    "payment_mode": modes[0],
                }
            )
            if len(modes) == 1
            else f"{base_url}/upgrade/club/plan"
        )
    else:
        raise ValueError("Unknown Club assessment outcome")

    details = {"Club location": club_name}
    if primary_technique_focus:
        details["Main coaching focus"] = primary_technique_focus
    if first_club_milestone:
        details["First goal"] = first_club_milestone
    body = (
        f"Hi {member_name},\n\n{message}\n\n"
        + "\n".join(f"{label}: {value}" for label, value in details.items())
        + f"\n\n{next_step}\n\n{cta}: {url}\n\nThe SwimBuddz Team"
    )
    html_body = wrap_html(
        title=escape(title),
        preheader=escape(message),
        body_html=(
            f"<p>Hi {escape(member_name)},</p><p>{escape(message)}</p>"
            + detail_box({label: escape(value) for label, value in details.items()})
            + f"<p>{escape(next_step)}</p>"
            + cta_button(cta, escape(url, quote=True))
            + sign_off()
        ),
    )
    return await send_email(to_email, title, body, html_body)
