"""ARQ attendance reconciliation.

Included Academy classes default enrolled students to Present via a bounded
48-hour lookback every 15 minutes, plus a full active-cohort history repair
at startup and once daily. Saved coach/admin exceptions always win.
The evening reminder and nightly legacy booking sweep remain in place for
other session kinds; the sweep never invents an Academy absence.
"""

from arq import cron

from libs.common.arq_config import get_redis_settings
from libs.common.logging import get_logger

logger = get_logger(__name__)
RECENT_ACADEMY_DEFAULTS_LOOKBACK_HOURS = 48


async def task_reconcile_recent_academy_defaults(ctx: dict):
    from services.attendance_service.services.academy_defaults import (
        reconcile_academy_defaults,
    )

    return await reconcile_academy_defaults(
        lookback_hours=RECENT_ACADEMY_DEFAULTS_LOOKBACK_HOURS
    )


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


async def task_sync_swim_rewards(ctx: dict):
    from services.attendance_service.services.swim_rewards import sync_due_swims

    return await sync_due_swims()


class WorkerSettings:
    redis_settings = get_redis_settings()
    queue_name = "arq:attendance"

    functions = [
        task_sweep_no_show_bookings,
        task_notify_stale_attendance,
        task_reconcile_recent_academy_defaults,
        task_reconcile_academy_defaults,
        task_sync_swim_rewards,
    ]

    cron_jobs = [
        # Reconcile post-session swim Bubbles every 10 minutes (and on startup)
        # instead of every minute on NullPool.
        cron(
            task_sync_swim_rewards,
            minute={2, 12, 22, 32, 42, 52},
            run_at_startup=True,
        ),
        # Recent-window default Present reconciliation for newly started classes.
        cron(
            task_reconcile_recent_academy_defaults,
            minute={0, 15, 30, 45},
            run_at_startup=False,
        ),
        # Full active-cohort historical safety repair once daily (03:15 WAT)
        # and on worker startup.
        cron(
            task_reconcile_academy_defaults,
            hour={2},
            minute={15},
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
