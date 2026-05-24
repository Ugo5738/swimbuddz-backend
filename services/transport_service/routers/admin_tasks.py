"""Admin-triggered manual task endpoints (transport)."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.common.logging import get_logger
from libs.db.session import get_async_db
from services.transport_service.services.chat_reconciliation import (
    reconcile_trip_chat_memberships,
)

router = APIRouter(prefix="/transport", tags=["admin-tasks"])
logger = get_logger(__name__)


@router.post("/admin/tasks/reconcile-trip-chat-memberships")
async def trigger_trip_chat_reconciliation(
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Manually run the trip chat reconciliation pass.

    Same logic the transport-worker runs hourly (and at startup). Use to
    immediately heal drift after a chat-service outage or known missed
    hook, without waiting for the next cron tick.
    """
    counters = await reconcile_trip_chat_memberships(db)
    return counters
