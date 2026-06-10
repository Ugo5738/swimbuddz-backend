"""ARQ worker for pools_service background tasks.

Runs the weather pre-fetch cron: every 3 hours it caches each active pool's
multi-day forecast (the "snapshot") so member/admin reads hit warm storage and
proactive rain alerts (future) have data to act on. Run with:

    arq services.pools_service.worker.WorkerSettings

Every-3-hours matches how often the upstream model updates. ``run_at_startup``
seeds the cache immediately on boot (handy in dev and after a deploy).
"""

from arq import cron

from libs.common.arq_config import get_redis_settings
from libs.common.logging import get_logger

logger = get_logger(__name__)


async def task_refresh_pool_forecasts(ctx: dict):
    """Pre-fetch and cache forecasts for all active pools."""
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from libs.common.config import get_settings
    from services.pools_service.weather.refresh import refresh_all_pools

    settings = get_settings()
    engine = create_async_engine(settings.DATABASE_URL)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    try:
        async with async_session() as session:
            result = await refresh_all_pools(session)
        logger.info("pools.weather prefetch cycle complete: %s", result)
        return result
    finally:
        await engine.dispose()


class WorkerSettings:
    """ARQ worker settings + cron schedule."""

    redis_settings = get_redis_settings()
    queue_name = "arq:pools"

    functions = [task_refresh_pool_forecasts]

    cron_jobs = [
        # Every 3 hours, on the hour (UTC). Open-Meteo refreshes on a similar cadence.
        cron(
            task_refresh_pool_forecasts,
            hour={0, 3, 6, 9, 12, 15, 18, 21},
            minute=0,
            run_at_startup=True,
        ),
    ]
