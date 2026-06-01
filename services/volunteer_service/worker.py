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
    """Select and feature the previous month's Volunteer of the Month."""
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from libs.common.config import get_settings
    from services.volunteer_service.services import apply_monthly_volunteer_spotlight

    settings = get_settings()
    engine = create_async_engine(settings.DATABASE_URL)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    try:
        async with async_session() as session:
            result = await apply_monthly_volunteer_spotlight(session)
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
