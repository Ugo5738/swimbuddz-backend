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


async def task_publish_scheduled_content(ctx: dict):
    """Publish content posts whose scheduled_for time has arrived."""
    from services.communications_service.tasks import publish_scheduled_content

    logger.info("Running: publish_scheduled_content")
    await publish_scheduled_content()


async def task_generate_content_images(ctx: dict):
    """Generate featured images for content posts using DALL-E."""
    from services.communications_service.tasks import generate_content_images

    logger.info("Running: generate_content_images")
    await generate_content_images()


# ── Worker configuration ──


class WorkerSettings:
    """ARQ worker settings with cron job schedules."""

    redis_settings = get_redis_settings()
    queue_name = "arq:communications"

    # Register all task functions so ARQ can discover them
    functions = [
        task_process_pending_notifications,
        task_send_weekly_session_digest,
        task_publish_scheduled_content,
        task_generate_content_images,
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
        # Publish scheduled content posts (hourly check)
        # Posts are typically scheduled for Wednesday 7 AM WAT (6 AM UTC)
        # but we check hourly to catch any scheduled time
        cron(
            task_publish_scheduled_content,
            minute=0,  # Every hour at :00
            run_at_startup=False,
        ),
        # Generate featured images for content posts (Tuesday 5 AM UTC / 6 AM WAT)
        # Runs day before publish day so images are ready when posts go live Wednesday
        cron(
            task_generate_content_images,
            weekday=1,  # Tuesday
            hour=5,
            minute=0,
            run_at_startup=False,
        ),
    ]
