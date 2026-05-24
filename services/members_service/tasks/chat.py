"""Chat-related background tasks (members → pods + locations)."""

from libs.common.logging import get_logger
from libs.db.session import get_async_db
from services.members_service.services.chat_reconciliation import (
    reconcile_location_chat_memberships,
    reconcile_pod_chat_memberships,
)

logger = get_logger(__name__)


async def reconcile_chat_memberships():
    """Periodic safety net for pod + location chat memberships.

    See [chat_reconciliation.py](../services/chat_reconciliation.py) for
    the rationale. Runs both parent-type walks in one pass; idempotent.
    """
    async for db in get_async_db():
        try:
            await reconcile_pod_chat_memberships(db)
            await reconcile_location_chat_memberships(db)
        finally:
            await db.close()
        break
