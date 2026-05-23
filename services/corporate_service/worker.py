"""ARQ worker for corporate_service background tasks.

Currently runs one cron job: ``run_outreach_cycle`` — the daily-tick
scheduler that fires the next outreach email for any contact whose gap
is up. Run with:

    arq services.corporate_service.worker.WorkerSettings

Scheduling chose 07:00 UTC = 08:00 WAT — outside Lagos working hours
breakfast slot so the email lands at the start of the day. The 7-day gap
floor in services/outreach.py keeps it safe to nudge if the cron misses
a day.
"""

from arq import cron

from libs.common.arq_config import get_redis_settings
from libs.common.logging import get_logger

logger = get_logger(__name__)


async def task_run_outreach_cycle(ctx: dict):
    """Process all due outreach emails and log results."""
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from libs.common.config import get_settings
    from services.corporate_service.services.outreach import run_outreach_cycle

    settings = get_settings()
    engine = create_async_engine(settings.DATABASE_URL)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    try:
        async with async_session() as session:
            result = await run_outreach_cycle(session)
        logger.info(
            "corporate.outreach cycle complete: %s",
            result,
        )
    finally:
        await engine.dispose()


class WorkerSettings:
    """ARQ worker settings + cron schedule."""

    redis_settings = get_redis_settings()
    queue_name = "arq:corporate"

    functions = [task_run_outreach_cycle]

    cron_jobs = [
        # Run the outreach scheduler daily at 07:00 UTC = 08:00 WAT.
        cron(
            task_run_outreach_cycle,
            hour=7,
            minute=0,
            run_at_startup=False,
        ),
    ]
