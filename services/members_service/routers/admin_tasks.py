"""Admin-triggered manual task endpoints (members)."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.common.logging import get_logger
from libs.db.session import get_async_db
from services.members_service.services.chat_reconciliation import (
    reconcile_location_chat_memberships,
    reconcile_pod_chat_memberships,
)

router = APIRouter(tags=["admin-tasks"])
logger = get_logger(__name__)


@router.post("/admin/tasks/reconcile-pod-chat-memberships")
async def trigger_pod_chat_reconciliation(
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Manually run the pod chat reconciliation pass.

    Same logic the members-worker runs hourly (and at startup). Use to
    immediately heal drift after a chat-service outage or known missed
    hook, without waiting for the next cron tick.
    """
    counters = await reconcile_pod_chat_memberships(db)
    return counters


@router.post("/admin/tasks/reconcile-location-chat-memberships")
async def trigger_location_chat_reconciliation(
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Manually run the location (city) chat reconciliation pass.

    Walks every distinct ``Member.city`` and re-asserts the city's
    chat channel + every member assigned to that city. Idempotent.
    """
    counters = await reconcile_location_chat_memberships(db)
    return counters
