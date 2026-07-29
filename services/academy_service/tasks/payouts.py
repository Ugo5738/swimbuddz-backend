"""Background repair for billable cohort-extension payout schedules."""

from libs.common.logging import get_logger
from libs.db.session import get_async_db
from services.academy_service.services.payout_extension_reconciliation import (
    reconcile_billable_extension_payouts,
)

logger = get_logger(__name__)


async def reconcile_extension_payout_schedules() -> None:
    async for db in get_async_db():
        try:
            synced, failed = await reconcile_billable_extension_payouts(db)
            if synced or failed:
                logger.info(
                    "Billable extension payout reconciliation: synced=%d failed=%d",
                    synced,
                    failed,
                )
        except Exception:
            logger.error(
                "Billable extension payout reconciliation failed",
                exc_info=True,
            )
        finally:
            await db.close()
            break
