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
        logger.info(
            "volunteer.spotlight monthly rotation complete: %s",
            result,
        )
    finally:
        await engine.dispose()


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
