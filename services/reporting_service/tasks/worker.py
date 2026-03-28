"""ARQ worker for reporting service background tasks.

Schedules periodic tasks for quarterly report generation.
Run with: arq services.reporting_service.tasks.worker.WorkerSettings
"""

from arq import cron

from libs.common.arq_config import get_redis_settings
from libs.common.logging import get_logger

logger = get_logger(__name__)


async def task_generate_quarterly_snapshot(ctx: dict):
    """Generate quarterly snapshot for the most recently completed quarter."""
    from services.reporting_service.tasks.snapshot import run_quarterly_snapshot

    logger.info("Running: task_generate_quarterly_snapshot")
    await run_quarterly_snapshot()


class WorkerSettings:
    """ARQ worker settings with cron job schedules."""

    redis_settings = get_redis_settings()
    queue_name = "arq:reporting"

    functions = [
        task_generate_quarterly_snapshot,
    ]

    cron_jobs = [
        # Run on the 2nd day of each quarter-start month at 3 AM WAT
        # Jan 2, Apr 2, Jul 2, Oct 2
        cron(
            task_generate_quarterly_snapshot,
            month={1, 4, 7, 10},
            day=2,
            hour=3,
            minute=0,
            run_at_startup=False,
        ),
    ]
