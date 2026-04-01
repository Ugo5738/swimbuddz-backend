"""Internal endpoints for service-to-service communication.

These endpoints are called by other services (e.g. sessions_service) via HTTP
instead of cross-service Python imports, which break in containerised deployments.
"""

from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from libs.auth.dependencies import is_admin_or_service
from libs.auth.models import AuthUser
from services.communications_service.tasks import (
    cancel_session_notifications,
    schedule_session_notifications,
    send_session_announcement,
)

router = APIRouter(
    prefix="/internal/communications",
    tags=["internal"],
)


# ── Request schemas ──────────────────────────────────────────────────────


class SessionPublishRequest(BaseModel):
    session_id: str
    is_short_notice: bool = False
    short_notice_message: str = ""


class SessionCancelRequest(BaseModel):
    session_id: str
    cancellation_reason: str = ""


# ── Endpoints ────────────────────────────────────────────────────────────


@router.post("/session-published")
async def handle_session_published(
    body: SessionPublishRequest,
    current_user: AuthUser = Depends(is_admin_or_service),
) -> dict:
    """Trigger publish notifications for a session.

    Called by sessions_service after a session transitions from draft → scheduled.
    Schedules reminders and sends the immediate announcement email + in-app notification.
    """
    session_uuid = UUID(body.session_id)

    await schedule_session_notifications(
        session_id=session_uuid,
        is_short_notice=body.is_short_notice,
    )

    await send_session_announcement(
        session_id=session_uuid,
        short_notice_message=body.short_notice_message,
    )

    return {"ok": True}


@router.post("/session-cancelled")
async def handle_session_cancelled(
    body: SessionCancelRequest,
    current_user: AuthUser = Depends(is_admin_or_service),
) -> dict:
    """Trigger cancellation notifications for a session.

    Called by sessions_service after a session is cancelled.
    Cancels pending reminders and sends cancellation notices.
    """
    session_uuid = UUID(body.session_id)

    await cancel_session_notifications(
        session_id=session_uuid,
        cancellation_reason=body.cancellation_reason,
    )

    return {"ok": True}
