"""Chat-related background tasks (events)."""

from libs.common.logging import get_logger
from libs.db.session import get_async_db
from services.events_service.services.chat_reconciliation import (
    reconcile_event_chat_memberships,
)

logger = get_logger(__name__)


async def reconcile_chat_memberships():
    """Periodic safety net for event chat memberships.

    See [chat_reconciliation.py](../services/chat_reconciliation.py) for
    the rationale. Runs against events in the active window; idempotent.
    """
    async for db in get_async_db():
        try:
            await reconcile_event_chat_memberships(db)
        finally:
            await db.close()
        break
