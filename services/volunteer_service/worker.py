"""ARQ worker for volunteer_service background tasks.

Runs the monthly volunteer spotlight rotation just after a month closes.
Run with:

    arq services.volunteer_service.worker.WorkerSettings
"""

from arq import cron

from libs.common.arq_config import get_redis_settings
from libs.common.logging import get_logger

logger = get_logger(__name__)


async def task_apply_monthly_volunteer_spotlight(ctx: dict):
    """Select and feature the previous month's Volunteer of the Month.

    Excludes the founder/staff allowlist and any coaches — the spotlight is a
    community award, so staff aren't eligible. Coach status is resolved via
    members_service (get_coach_profile); a transient lookup failure is treated
    as "not a coach" so it never blocks the rotation.
    """
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from libs.common.config import get_settings
    from libs.common.service_client import get_coach_profile
    from services.volunteer_service.services import (
        SPOTLIGHT_EXCLUDED_MEMBER_IDS,
        apply_monthly_volunteer_spotlight,
    )

    async def _is_coach(member_id) -> bool:
        try:
            profile = await get_coach_profile(
                str(member_id), calling_service="volunteer"
            )
            return profile is not None
        except Exception:  # noqa: BLE001 - never block the spotlight on a lookup error
            logger.warning(
                "coach lookup failed for %s; treating as non-coach", member_id
            )
            return False

    settings = get_settings()
    engine = create_async_engine(settings.DATABASE_URL)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    try:
        async with async_session() as session:
            result = await apply_monthly_volunteer_spotlight(
                session,
                excluded_member_ids=SPOTLIGHT_EXCLUDED_MEMBER_IDS,
                is_coach=_is_coach,
            )
            if result.member_id is not None:
                await _announce_volunteer_of_the_month(session, result)
        logger.info(
            "volunteer.spotlight monthly rotation complete: %s",
            result,
        )
    finally:
        await engine.dispose()


async def _announce_volunteer_of_the_month(session, result) -> None:
    """Best-effort in-app notifications when a new Volunteer of the Month is set.

    Congratulates the winner and announces to the rest of the active volunteer
    crew. ``dispatch_notification`` already swallows delivery errors; the outer
    guard covers the name/audience lookups so a hiccup never breaks the rotation.
    (Broader reach — the WhatsApp groups — is still posted manually.)
    """
    from sqlalchemy import select

    from libs.common.service_client import dispatch_notification, get_members_bulk
    from services.volunteer_service.models import VolunteerProfile

    try:
        winner_id = str(result.member_id)
        month_label = result.period_start.strftime("%B %Y")

        members = await get_members_bulk([winner_id], calling_service="volunteer")
        winner_name = "Our volunteer"
        for m in members:
            if str(m.get("id")) == winner_id:
                winner_name = m.get("first_name") or m.get("full_name") or winner_name
                break

        # Congratulate the winner
        await dispatch_notification(
            type="volunteer_of_the_month_winner",
            category="volunteer",
            member_ids=[winner_id],
            title="🏆 You're Volunteer of the Month!",
            body=(
                f"Congratulations, {winner_name}! You're SwimBuddz Volunteer of "
                f"the Month for {month_label}. Thank you for showing up for the "
                f"community. 💙"
            ),
            action_url="/community/volunteers",
            icon="trophy",
            channels=["in_app"],
            calling_service="volunteer",
        )

        # Announce to the rest of the active volunteer crew
        active_ids = (
            (
                await session.execute(
                    select(VolunteerProfile.member_id).where(
                        VolunteerProfile.is_active.is_(True)
                    )
                )
            )
            .scalars()
            .all()
        )
        audience = [str(mid) for mid in active_ids if str(mid) != winner_id]
        if audience:
            await dispatch_notification(
                type="volunteer_of_the_month_announcement",
                category="volunteer",
                member_ids=audience,
                title="🏆 Volunteer of the Month",
                body=(
                    f"Big congrats to {winner_name}, our Volunteer of the Month "
                    f"for {month_label}! 🎉 Could be you next month."
                ),
                action_url="/community/volunteers",
                icon="trophy",
                channels=["in_app"],
                calling_service="volunteer",
            )
    except Exception as exc:  # noqa: BLE001 - notifications must never break the rotation
        logger.warning("volunteer spotlight announcement failed: %s", exc)


class WorkerSettings:
    """ARQ worker settings + cron schedule."""

    redis_settings = get_redis_settings()
    queue_name = "arq:volunteer"

    functions = [task_apply_monthly_volunteer_spotlight]

    cron_jobs = [
        # Run just after the month closes: 00:10 UTC = 01:10 WAT on day 1.
        cron(
            task_apply_monthly_volunteer_spotlight,
            day=1,
            hour=0,
            minute=10,
            run_at_startup=False,
        ),
    ]
