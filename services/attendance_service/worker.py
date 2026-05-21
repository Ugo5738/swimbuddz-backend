"""ARQ worker for attendance reconciliation.

Runs two cron jobs:

* **notify_stale_attendance** — daily at 19:00 UTC (~20:00 WAT). Pings
  coaches and admins about sessions whose attendance is still unmarked
  ~24h after they ended, BEFORE the auto-mark sweep below has a chance
  to flip them to ABSENT.
* **sweep_no_show_bookings** — daily at 02:45 UTC (~03:45 WAT). For
  CONFIRMED bookings whose session ended without a matching
  AttendanceRecord, creates AttendanceRecord(status=ABSENT). See
  docs/design/A1_SESSION_DISCRIMINATOR_REFACTOR.md §C.

The notify job is intentionally scheduled ~7h before the sweep so the
coach has an evening window to mark attendance manually before the
system fills in ABSENT rows on their behalf.
"""

from arq import cron

from libs.common.arq_config import get_redis_settings
from libs.common.logging import get_logger

logger = get_logger(__name__)


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

    functions = [task_sweep_no_show_bookings, task_notify_stale_attendance]

    cron_jobs = [
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
