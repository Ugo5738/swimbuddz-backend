"""ARQ worker for communications service background tasks.

Schedules periodic tasks for session notifications via ARQ cron jobs backed by Redis.
Run with: arq services.communications_service.worker.WorkerSettings
"""

from arq import cron
from libs.common.arq_config import get_redis_settings
from libs.common.logging import get_logger

logger = get_logger(__name__)


# ── Wrapper functions (ARQ requires top-level async callables) ──


async def task_process_pending_notifications(ctx: dict):
    """Process pending scheduled session notifications."""
    from services.communications_service.tasks import process_pending_notifications

    logger.info("Running: process_pending_notifications")
    await process_pending_notifications()


async def task_send_weekly_session_digest(ctx: dict):
    """Send weekly session digest to subscribed members."""
    from services.communications_service.tasks import send_weekly_session_digest

    logger.info("Running: send_weekly_session_digest")
    await send_weekly_session_digest()


# ── Worker configuration ──


class WorkerSettings:
    """ARQ worker settings with cron job schedules."""

    redis_settings = get_redis_settings()

    # Register all task functions so ARQ can discover them
    functions = [
        task_process_pending_notifications,
        task_send_weekly_session_digest,
    ]

    cron_jobs = [
        # Process pending notifications every 5 minutes
        cron(
            task_process_pending_notifications,
            minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55},
            run_at_startup=False,
        ),
        # Weekly session digest (Sunday 8 AM WAT / 7 AM UTC)
        cron(
            task_send_weekly_session_digest,
            weekday=6,  # Sunday
            hour=7,
            minute=0,
            run_at_startup=False,
        ),
    ]
