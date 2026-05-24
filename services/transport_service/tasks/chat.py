"""Chat-related background tasks (transport)."""

from libs.common.logging import get_logger
from libs.db.session import get_async_db
from services.transport_service.services.chat_reconciliation import (
    reconcile_trip_chat_memberships,
)

logger = get_logger(__name__)


async def reconcile_chat_memberships():
    """Periodic safety net for trip chat memberships.

    See [chat_reconciliation.py](../services/chat_reconciliation.py) for
    the rationale. Runs against trips with recent bookings; idempotent.
    """
    async for db in get_async_db():
        try:
            await reconcile_trip_chat_memberships(db)
        finally:
            await db.close()
        break
