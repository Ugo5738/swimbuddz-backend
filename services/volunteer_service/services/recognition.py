"""Volunteer-of-the-month recognition delivery."""

from __future__ import annotations

import uuid
from datetime import date
from html import escape

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.logging import get_logger
from libs.common.member_utils import resolve_members_with_photos
from libs.common.service_client import dispatch_notification
from services.volunteer_service.models import VolunteerProfile

logger = get_logger(__name__)


def _winner_email_html(
    *,
    winner_name: str,
    month_label: str,
    monthly_hours: float | None,
    photo_url: str | None,
    spotlight_quote: str | None,
) -> str:
    photo = ""
    if photo_url:
        photo = (
            f'<img src="{escape(photo_url, quote=True)}" '
            f'alt="{escape(winner_name, quote=True)}" '
            'style="display:block;width:180px;height:180px;object-fit:cover;'
            'border-radius:999px;margin:20px auto;"/>'
        )
    contribution = ""
    if monthly_hours is not None and monthly_hours > 0:
        contribution = (
            f"<p><strong>{monthly_hours:g} volunteer hours</strong> contributed "
            f"during {escape(month_label)}.</p>"
        )
    quote = ""
    if spotlight_quote:
        quote = (
            '<blockquote style="margin:20px 0;padding:14px 18px;'
            'border-left:4px solid #0891b2;background:#f0f9ff;">'
            f"{escape(spotlight_quote)}</blockquote>"
        )
    return (
        f"<h1>Congratulations, {escape(winner_name)}! 🏆</h1>"
        f"{photo}"
        "<p>You are SwimBuddz Volunteer of the Month. Thank you for the care, "
        "reliability, and energy you bring to our community.</p>"
        f"{contribution}{quote}"
        '<p><a href="https://www.swimbuddz.com/community">View the community '
        "spotlight</a></p>"
    )


async def announce_volunteer_of_the_month(
    db: AsyncSession,
    *,
    member_id: uuid.UUID,
    period_start: date,
    monthly_hours: float | None = None,
) -> None:
    """Email the winner and notify the active volunteer crew.

    The wider SwimBuddz community sees the same recognition in the next weekly
    digest; this immediate fan-out stays personal for the winner and lightweight
    for the volunteer crew.
    """
    try:
        member_map = await resolve_members_with_photos([member_id])
        winner = member_map.get(str(member_id))
        if winner is None:
            logger.warning(
                "Skipping volunteer recognition; member lookup failed for %s",
                member_id,
            )
            return

        profile = (
            await db.execute(
                select(VolunteerProfile).where(VolunteerProfile.member_id == member_id)
            )
        ).scalar_one_or_none()
        month_label = period_start.strftime("%B %Y")
        winner_name = winner.first_name or winner.full_name or "Volunteer"
        winner_body = (
            f"Congratulations, {winner_name}! You're SwimBuddz Volunteer of "
            f"the Month for {month_label}. Thank you for showing up for the "
            "community. 💙"
        )

        await dispatch_notification(
            type="volunteer_of_the_month_winner",
            category="volunteer",
            member_ids=[str(member_id)],
            title="🏆 You're Volunteer of the Month!",
            body=winner_body,
            action_url="/community",
            icon="trophy",
            channels=["in_app", "email"],
            email_template="volunteer_of_the_month",
            email_data={
                "to_email": winner.email,
                "body": winner_body,
                "html_content": _winner_email_html(
                    winner_name=winner_name,
                    month_label=month_label,
                    monthly_hours=monthly_hours,
                    photo_url=winner.profile_photo_url,
                    spotlight_quote=profile.spotlight_quote if profile else None,
                ),
            },
            calling_service="volunteer",
        )

        active_ids = (
            (
                await db.execute(
                    select(VolunteerProfile.member_id).where(
                        VolunteerProfile.is_active.is_(True)
                    )
                )
            )
            .scalars()
            .all()
        )
        audience = [str(mid) for mid in active_ids if mid != member_id]
        if audience:
            await dispatch_notification(
                type="volunteer_of_the_month_announcement",
                category="volunteer",
                member_ids=audience,
                title="🏆 Volunteer of the Month",
                body=(
                    f"Big congrats to {winner_name}, our Volunteer of the Month "
                    f"for {month_label}! 🎉"
                ),
                action_url="/community",
                icon="trophy",
                channels=["in_app"],
                calling_service="volunteer",
            )
    except Exception as exc:  # noqa: BLE001 - recognition must not undo selection
        logger.warning("Volunteer spotlight announcement failed: %s", exc)


__all__ = ["announce_volunteer_of_the_month"]
