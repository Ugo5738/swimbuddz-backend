"""ARQ worker for reporting service background tasks.

Schedules periodic tasks for quarterly report generation and flywheel
snapshot computation.

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


async def task_refresh_current_quarter_reports(ctx: dict):
    """Refresh member and community reporting throughout the active quarter.

    Runs in the reporting worker rather than on a member page request so a
    single dashboard visit never launches a cross-service fan-out for everyone.
    """
    from libs.db.session import AsyncSessionLocal
    from services.reporting_service.services.aggregator import (
        compute_all_member_reports,
        compute_community_stats,
    )
    from services.reporting_service.services.quarter_utils import current_quarter

    year, quarter = current_quarter()
    async with AsyncSessionLocal() as db:
        count = await compute_all_member_reports(year, quarter, db)
        await compute_community_stats(year, quarter, db)
    logger.info("Refreshed live Q%s %s reports for %s swimmers", quarter, year, count)


async def task_refresh_cohort_fill_snapshots(ctx: dict):
    """Snapshot cohort fill state for all OPEN/ACTIVE cohorts."""
    from services.reporting_service.tasks.flywheel import compute_cohort_fill_snapshots

    logger.info("Running: task_refresh_cohort_fill_snapshots")
    await compute_cohort_fill_snapshots()


async def task_refresh_funnel_conversions(ctx: dict):
    """Compute community→club, club→academy, community→academy conversions."""
    from services.reporting_service.tasks.flywheel import compute_funnel_conversions

    logger.info("Running: task_refresh_funnel_conversions")
    await compute_funnel_conversions()


async def task_refresh_wallet_ecosystem(ctx: dict):
    """Snapshot wallet cross-service spend ecosystem."""
    from services.reporting_service.tasks.flywheel import (
        compute_wallet_ecosystem_snapshot,
    )

    logger.info("Running: task_refresh_wallet_ecosystem")
    await compute_wallet_ecosystem_snapshot()


async def task_refresh_all_flywheel(ctx: dict):
    """Run all three flywheel snapshot tasks (manual trigger from admin UI)."""
    from services.reporting_service.tasks.flywheel import refresh_all_flywheel_snapshots

    logger.info("Running: task_refresh_all_flywheel")
    return await refresh_all_flywheel_snapshots()


async def task_refresh_content_acquisition(ctx: dict):
    """Record confirmed member registrations by editorial source."""
    from services.reporting_service.tasks.content_acquisition import (
        refresh_content_acquisition,
    )

    return await refresh_content_acquisition()


class WorkerSettings:
    """ARQ worker settings with cron job schedules."""

    redis_settings = get_redis_settings()
    queue_name = "arq:reporting"

    functions = [
        task_generate_quarterly_snapshot,
        task_refresh_current_quarter_reports,
        task_refresh_cohort_fill_snapshots,
        task_refresh_funnel_conversions,
        task_refresh_wallet_ecosystem,
        task_refresh_all_flywheel,
        task_refresh_content_acquisition,
    ]

    cron_jobs = [
        # Current-quarter community stats and leaderboard, refreshed hourly.
        cron(task_refresh_current_quarter_reports, minute=10, run_at_startup=True),
        cron(
            task_refresh_content_acquisition,
            hour=5,
            minute=30,
            run_at_startup=False,
        ),
        # Quarterly: run on 2nd day of each quarter-start month at 3 AM WAT
        cron(
            task_generate_quarterly_snapshot,
            month={1, 4, 7, 10},
            day=2,
            hour=3,
            minute=0,
            run_at_startup=False,
        ),
        # Daily: refresh cohort fill snapshots at 06:00 WAT
        cron(
            task_refresh_cohort_fill_snapshots,
            hour=6,
            minute=0,
            run_at_startup=False,
        ),
        # Weekly: refresh funnel conversions every Monday 04:00 WAT
        cron(
            task_refresh_funnel_conversions,
            weekday="mon",
            hour=4,
            minute=0,
            run_at_startup=False,
        ),
        # Weekly: refresh wallet ecosystem snapshot every Monday 04:30 WAT
        cron(
            task_refresh_wallet_ecosystem,
            weekday="mon",
            hour=4,
            minute=30,
            run_at_startup=False,
        ),
    ]
