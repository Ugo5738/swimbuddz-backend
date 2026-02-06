"""ARQ worker for academy service background tasks.

Schedules periodic tasks via ARQ cron jobs backed by Redis.
Run with: arq services.academy_service.worker.WorkerSettings
"""

from arq import cron
from libs.common.arq_config import get_redis_settings
from libs.common.logging import get_logger

logger = get_logger(__name__)


# ── Wrapper functions (ARQ requires top-level async callables) ──


async def task_send_enrollment_reminders(ctx: dict):
    """Send enrollment reminders for upcoming cohorts."""
    from services.academy_service.tasks import send_enrollment_reminders

    logger.info("Running: send_enrollment_reminders")
    await send_enrollment_reminders()


async def task_process_waitlist(ctx: dict):
    """Promote waitlisted students when spots open."""
    from services.academy_service.tasks import process_waitlist

    logger.info("Running: process_waitlist")
    await process_waitlist()


async def task_transition_cohort_statuses(ctx: dict):
    """Transition cohort statuses based on dates."""
    from services.academy_service.tasks import transition_cohort_statuses

    logger.info("Running: transition_cohort_statuses")
    await transition_cohort_statuses()


async def task_check_and_issue_certificates(ctx: dict):
    """Issue certificates for completed enrollments."""
    from services.academy_service.tasks import check_and_issue_certificates

    logger.info("Running: check_and_issue_certificates")
    await check_and_issue_certificates()


async def task_send_weekly_progress_reports(ctx: dict):
    """Send weekly progress report emails."""
    from services.academy_service.tasks import send_weekly_progress_reports

    logger.info("Running: send_weekly_progress_reports")
    await send_weekly_progress_reports()


async def task_check_attendance_and_notify(ctx: dict):
    """Check attendance patterns and notify coaches."""
    from services.academy_service.tasks import check_attendance_and_notify

    logger.info("Running: check_attendance_and_notify")
    await check_attendance_and_notify()


# ── Worker configuration ──


class WorkerSettings:
    """ARQ worker settings with cron job schedules."""

    redis_settings = get_redis_settings()

    # Register all task functions so ARQ can discover them
    functions = [
        task_send_enrollment_reminders,
        task_process_waitlist,
        task_transition_cohort_statuses,
        task_check_and_issue_certificates,
        task_send_weekly_progress_reports,
        task_check_attendance_and_notify,
    ]

    cron_jobs = [
        # Hourly tasks
        cron(
            task_send_enrollment_reminders,
            minute=0,
            run_at_startup=False,
        ),
        cron(
            task_process_waitlist,
            minute=15,
            run_at_startup=False,
        ),
        cron(
            task_transition_cohort_statuses,
            minute=30,
            run_at_startup=False,
        ),
        # Daily tasks (6 AM UTC)
        cron(
            task_check_and_issue_certificates,
            hour=6,
            minute=0,
            run_at_startup=False,
        ),
        # Weekly tasks (Sunday)
        cron(
            task_send_weekly_progress_reports,
            weekday=6,  # Sunday
            hour=8,
            minute=0,
            run_at_startup=False,
        ),
        cron(
            task_check_attendance_and_notify,
            weekday=6,  # Sunday
            hour=9,
            minute=0,
            run_at_startup=False,
        ),
    ]
