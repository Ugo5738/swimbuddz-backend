"""Durable synchronization of billable cohort extensions to coach payouts."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from libs.common.service_client import extend_recurring_payouts_for_cohort
from services.academy_service.models import (
    CohortExtensionRequest,
    ExtensionRequestStatus,
)

logger = get_logger(__name__)


async def sync_billable_extension_payout(
    db: AsyncSession, extension: CohortExtensionRequest
) -> bool:
    """Sync one approved billable extension; safe to retry."""
    if (
        extension.status != ExtensionRequestStatus.APPROVED
        or not extension.coach_payout_billable
    ):
        return False

    result = await extend_recurring_payouts_for_cohort(
        str(extension.cohort_id),
        current_end_date=extension.current_end_date.isoformat(),
        proposed_end_date=extension.proposed_end_date.isoformat(),
        calling_service="academy",
    )
    schedules = result.get("schedules") or []
    if not schedules:
        logger.info(
            "Billable extension %s has no recurring payout config yet; "
            "leaving it pending for hourly reconciliation",
            extension.id,
        )
        return False
    extension.coach_payout_synced_at = utc_now()
    await db.commit()
    logger.info(
        "Synced billable cohort extension %s to %d payout schedule(s)",
        extension.id,
        len(schedules),
    )
    return True


async def reconcile_billable_extension_payouts(
    db: AsyncSession,
) -> tuple[int, int]:
    """Retry approved billable extensions that have not reached payments."""
    result = await db.execute(
        select(CohortExtensionRequest)
        .where(
            CohortExtensionRequest.status == ExtensionRequestStatus.APPROVED,
            CohortExtensionRequest.coach_payout_billable.is_(True),
            CohortExtensionRequest.coach_payout_synced_at.is_(None),
        )
        .order_by(CohortExtensionRequest.reviewed_at.asc())
    )
    pending = list(result.scalars().all())
    synced = 0
    failed = 0
    for extension in pending:
        try:
            if await sync_billable_extension_payout(db, extension):
                synced += 1
        except Exception:
            await db.rollback()
            failed += 1
            logger.warning(
                "Billable extension payout sync failed; hourly reconciliation "
                "will retry: extension=%s cohort=%s",
                extension.id,
                extension.cohort_id,
                exc_info=True,
            )
    return synced, failed
