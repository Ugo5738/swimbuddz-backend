"""ARQ worker for sessions reconciliation.

Currently runs the PENDING-booking expiry sweep (A1 Phase 3.3). See
docs/design/A1_SESSION_DISCRIMINATOR_REFACTOR.md §C.
"""

from arq import cron
from libs.common.arq_config import get_redis_settings
from libs.common.logging import get_logger

logger = get_logger(__name__)


async def task_sweep_expired_pending_bookings(ctx: dict):
    from services.sessions_service.tasks import sweep_expired_pending_bookings

    logger.info("Running: sweep_expired_pending_bookings")
    await sweep_expired_pending_bookings()


async def task_sweep_complete_makeups(ctx: dict):
    from services.sessions_service.tasks import sweep_complete_makeups

    logger.info("Running: sweep_complete_makeups")
    await sweep_complete_makeups()


class WorkerSettings:
    redis_settings = get_redis_settings()
    queue_name = "arq:sessions"

    functions = [task_sweep_expired_pending_bookings, task_sweep_complete_makeups]

    cron_jobs = [
        # Every 5 minutes — flips PENDING bookings past their 15-min TTL
        # to EXPIRED so the seat is released back to other members.
        cron(
            task_sweep_expired_pending_bookings,
            minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55},
            run_at_startup=False,
        ),
        # Every 30 minutes — completes/forfeits make-ups whose scheduled
        # session has ended, based on the learner's attendance.
        cron(
            task_sweep_complete_makeups,
            minute={2, 32},
            run_at_startup=False,
        ),
    ]
