"""ARQ worker for ledger_service — nightly revenue recognition (design §10).

    arq services.ledger_service.worker.WorkerSettings

Once a night it (1) ensures recognition schedules exist for any deferred-revenue
entries booked since the last run (backfill is idempotent), then (2) posts the
earned delta for every active schedule (``DR deferred_revenue_* / CR revenue_*``).
Recognition is catch-up by elapsed time and idempotent per (schedule, month), so
missing a night just means the next run recognises a slightly larger delta — no
double-posting, no drift.
"""

from arq import cron

from libs.common.arq_config import get_redis_settings
from libs.common.logging import get_logger

logger = get_logger(__name__)


async def task_run_recognition(ctx: dict):
    """Backfill schedules + recognise earned revenue for the default org."""
    import uuid
    from datetime import date

    from libs.common.config import get_settings
    from services.ledger_service.services.recognition import (
        backfill_schedules,
        run_due_recognition,
    )
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    settings = get_settings()
    org_raw = (settings.LEDGER_DEFAULT_ORG_ID or "").strip()
    if not org_raw:
        logger.warning("ledger.recognition skipped: LEDGER_DEFAULT_ORG_ID unset")
        return
    org_id = uuid.UUID(org_raw)

    engine = create_async_engine(
        settings.DATABASE_URL, connect_args={"prepare_threshold": 0}
    )
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with async_session() as session:
            # Org RLS context — no-op under bypassrls, correct under a scoped role.
            await session.execute(
                text("SELECT set_config('app.current_org_id', :o, false)"),
                {"o": str(org_id)},
            )
            created = await backfill_schedules(session, org_id)
            summary = await run_due_recognition(session, org_id, date.today())
            await session.commit()
        logger.info(
            "ledger.recognition complete: schedules_backfilled=%s %s", created, summary
        )
    finally:
        await engine.dispose()


class WorkerSettings:
    """ARQ worker settings + cron schedule."""

    redis_settings = get_redis_settings()
    queue_name = "arq:ledger"

    functions = [task_run_recognition]

    cron_jobs = [
        # Nightly at 02:00 UTC = 03:00 WAT — off-hours, after the day's postings.
        cron(task_run_recognition, hour=2, minute=0, run_at_startup=False),
    ]
