"""ARQ worker for members service background tasks.

Schedules periodic tasks via ARQ cron jobs backed by Redis.
Run with: arq services.members_service.tasks.worker.WorkerSettings
"""

from arq import cron
from libs.common.arq_config import get_redis_settings
from libs.common.logging import get_logger

logger = get_logger(__name__)


# ── Wrapper functions (ARQ requires top-level async callables) ──


async def task_reconcile_chat_memberships(ctx: dict):
    """Re-assert pod chat channels + memberships against current assignments."""
    from services.members_service.tasks import reconcile_chat_memberships

    logger.info("Running: reconcile_chat_memberships")
    await reconcile_chat_memberships()


# ── Worker configuration ──


class WorkerSettings:
    """ARQ worker settings with cron job schedules."""

    redis_settings = get_redis_settings()
    queue_name = "arq:members"

    functions = [
        task_reconcile_chat_memberships,
    ]

    cron_jobs = [
        # Chat reconciliation safety net (CHAT_SERVICE_DESIGN.md §4.2).
        # Hourly at :55, plus once at worker startup so a fresh deploy
        # heals any drift accumulated while the worker was down.
        cron(
            task_reconcile_chat_memberships,
            minute=55,
            run_at_startup=True,
        ),
    ]
