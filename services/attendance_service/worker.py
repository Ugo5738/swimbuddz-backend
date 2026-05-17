"""ARQ worker for attendance reconciliation.

Currently runs the nightly NO_SHOW sweep — see
docs/design/A1_SESSION_DISCRIMINATOR_REFACTOR.md §C and
services.attendance_service.tasks.sweep_no_show_bookings for details.
"""

from arq import cron
from libs.common.arq_config import get_redis_settings
from libs.common.logging import get_logger

logger = get_logger(__name__)


async def task_sweep_no_show_bookings(ctx: dict):
    from services.attendance_service.tasks import sweep_no_show_bookings

    logger.info("Running: sweep_no_show_bookings")
    await sweep_no_show_bookings()


class WorkerSettings:
    redis_settings = get_redis_settings()
    queue_name = "arq:attendance"

    functions = [task_sweep_no_show_bookings]

    cron_jobs = [
        # Daily at 02:45 UTC (~03:45 WAT). Looks back 7 days by default.
        cron(
            task_sweep_no_show_bookings,
            hour={2},
            minute={45},
            run_at_startup=False,
        ),
    ]
