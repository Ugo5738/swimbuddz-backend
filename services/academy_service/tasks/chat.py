"""Chat-related background tasks (academy)."""

from libs.common.logging import get_logger
from libs.db.session import get_async_db
from services.academy_service.services.chat_reconciliation import (
    reconcile_cohort_chat_memberships,
)

logger = get_logger(__name__)


async def reconcile_chat_memberships():
    """Periodic safety net for cohort chat memberships.

    See [chat_reconciliation.py](../services/chat_reconciliation.py) for the
    rationale. Runs against every ENROLLED enrollment; idempotent.
    """
    async for db in get_async_db():
        try:
            await reconcile_cohort_chat_memberships(db)
        finally:
            await db.close()
        break
