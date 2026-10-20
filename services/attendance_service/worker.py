"""ARQ attendance reconciliation.

Included Academy classes default enrolled students to Present at startup and
every five minutes after a class starts. Saved coach/admin exceptions win.
The evening reminder and nightly legacy booking sweep remain in place for
other session kinds; the sweep never invents an Academy absence.
"""

from arq import cron

from libs.common.arq_config import get_redis_settings
from libs.common.logging import get_logger

logger = get_logger(__name__)


async def task_reconcile_academy_defaults(ctx: dict):
    from services.attendance_service.services.academy_defaults import (
        reconcile_academy_defaults,
    )

    return await reconcile_academy_defaults()


async def task_sweep_no_show_bookings(ctx: dict):
    from services.attendance_service.tasks import sweep_no_show_bookings

    logger.info("Running: sweep_no_show_bookings")
    await sweep_no_show_bookings()


async def task_notify_stale_attendance(ctx: dict):
    from services.attendance_service.tasks import notify_stale_attendance

    logger.info("Running: notify_stale_attendance")
    await notify_stale_attendance()


class WorkerSettings:
    redis_settings = get_redis_settings()
    queue_name = "arq:attendance"

    functions = [
        task_sweep_no_show_bookings,
        task_notify_stale_attendance,
        task_reconcile_academy_defaults,
    ]

    cron_jobs = [
        cron(
            task_reconcile_academy_defaults,
            minute=set(range(0, 60, 5)),
            run_at_startup=True,
        ),
        # Stale-attendance nudge first (evening of the day after session)
        # so coaches can mark before the sweep auto-flips to ABSENT.
        cron(
            task_notify_stale_attendance,
            hour={19},
            minute={0},
            run_at_startup=False,
        ),
        # No-show auto-mark. Looks back 7 days by default.
        cron(
            task_sweep_no_show_bookings,
            hour={2},
            minute={45},
            run_at_startup=False,
        ),
    ]
