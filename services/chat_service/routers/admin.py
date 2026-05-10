"""Admin & moderator chat endpoints.

All paths under `/admin/chat/...`; the gateway proxies `/api/v1/admin/chat/*`.
Most endpoints require `require_admin`. Hard-deleting a message in a channel
flagged as containing minors additionally requires `require_safeguarding_admin`
— enforced inline so the OpenAPI signature stays uniform.
"""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import (
    require_admin,
    require_safeguarding_admin,
)
from libs.auth.models import AuthUser
from libs.common.logging import get_logger
from libs.common.service_client import get_member_by_auth_id
from libs.db.session import get_async_db

from services.chat_service.models import ChatChannel, ChatMessage
from services.chat_service.models.enums import ReportReason, ReportStatus
from services.chat_service.schemas import (
    AdminMemberRoleUpdateRequest,
    AdminMessageDeleteRequest,
    AuditLogPage,
    ChannelDetail,
    ChannelMemberOut,
    ReportListItem,
    ReportOut,
    ReportResolveRequest,
)
from services.chat_service.services import admin_ops, channel_ops, membership_ops
from services.chat_service.services.permissions import get_channel_or_404

logger = get_logger(__name__)

router = APIRouter(prefix="/admin/chat", tags=["admin-chat"])

_CALLING_SERVICE = "chat-admin"


async def _resolve_actor_member_id(current_user: AuthUser) -> Optional[uuid.UUID]:
    """Best-effort resolve of the admin's member_id for audit attribution.

    Returns None if the admin doesn't have a members-service profile (e.g.
    a service-role token, or a Supabase admin without a member row). Audit
    rows tolerate `actor_id=None` for system-attributed events."""
    member = await get_member_by_auth_id(
        current_user.user_id, calling_service=_CALLING_SERVICE
    )
    if not member or "id" not in member:
        return None
    try:
        return uuid.UUID(member["id"])
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Channels
# ---------------------------------------------------------------------------


@router.get("/channels/{channel_id}", response_model=ChannelDetail)
async def admin_get_channel(
    channel_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Admin channel detail. Doesn't require membership — admins can inspect
    any channel for moderation. Per-caller fields (`my_role`, `unread_count`)
    fall back to defaults since the admin isn't a member."""
    channel = await get_channel_or_404(db, channel_id)
    return await channel_ops.get_channel_detail(db, channel, membership=None)


@router.post(
    "/channels/{channel_id}/archive",
    response_model=ChannelDetail,
)
async def admin_archive_channel(
    channel_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    actor_id = await _resolve_actor_member_id(current_user)
    channel = await admin_ops.archive_channel(
        db, channel_id=channel_id, actor_id=actor_id
    )
    return await channel_ops.get_channel_detail(db, channel, membership=None)


# ---------------------------------------------------------------------------
# Members
# ---------------------------------------------------------------------------


@router.patch(
    "/channels/{channel_id}/members/{member_id}",
    response_model=ChannelMemberOut,
)
async def admin_update_member_role(
    channel_id: uuid.UUID,
    member_id: uuid.UUID,
    body: AdminMemberRoleUpdateRequest,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    actor_id = await _resolve_actor_member_id(current_user)
    return await admin_ops.update_member_role(
        db,
        channel_id=channel_id,
        member_id=member_id,
        new_role=body.role,
        actor_id=actor_id,
    )


@router.delete(
    "/channels/{channel_id}/members/{member_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def admin_remove_member(
    channel_id: uuid.UUID,
    member_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Admin soft-removes a member. Use the internal `memberships/reconcile`
    endpoint instead if the removal is driven by an upstream parent change
    (enrollment cancelled, etc.) so derived_from stays accurate."""
    channel = await get_channel_or_404(db, channel_id)
    actor_id = await _resolve_actor_member_id(current_user)
    await membership_ops.remove_member(
        db, channel=channel, member_id=member_id, actor_id=actor_id
    )


# ---------------------------------------------------------------------------
# Messages — hard delete
# ---------------------------------------------------------------------------


async def _gate_hard_delete(
    db: AsyncSession,
    *,
    message_id: uuid.UUID,
    current_user: AuthUser,
) -> None:
    """Enforce the rule from design §6.1 #5: hard-delete in a channel
    containing minors requires safeguarding_admin (admin alone is not enough)."""
    msg = await db.get(ChatMessage, message_id)
    if msg is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Message not found"
        )
    channel = await db.get(ChatChannel, msg.channel_id)
    if channel is not None and admin_ops.channel_has_minors(channel):
        if not (
            current_user.has_role("safeguarding_admin")
            or current_user.role == "service_role"
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Hard-delete in a channel containing minors requires "
                    "safeguarding_admin role."
                ),
            )


@router.delete(
    "/messages/{message_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def admin_hard_delete_message(
    message_id: uuid.UUID,
    body: AdminMessageDeleteRequest,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Hard-delete a message (row is removed; audit row retained).

    For minor channels, the safeguarding-admin check is enforced inline
    (a plain admin will get 403). The required `note` ends up in the audit
    payload."""
    await _gate_hard_delete(db, message_id=message_id, current_user=current_user)
    actor_id = await _resolve_actor_member_id(current_user)
    await admin_ops.hard_delete_message(
        db, message_id=message_id, actor_id=actor_id, note=body.note
    )


# ---------------------------------------------------------------------------
# Reports queue
# ---------------------------------------------------------------------------


@router.get("/reports", response_model=list[ReportListItem])
async def admin_list_reports(
    status_filter: Optional[ReportStatus] = Query(default=None, alias="status"),
    reason: Optional[ReportReason] = Query(default=None),
    assigned_to: Optional[uuid.UUID] = Query(default=None),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    return await admin_ops.list_reports(
        db,
        status_filter=status_filter,
        reason_filter=reason,
        assigned_to=assigned_to,
        skip=skip,
        limit=limit,
    )


@router.patch("/reports/{report_id}", response_model=ReportOut)
async def admin_resolve_report(
    report_id: uuid.UUID,
    body: ReportResolveRequest,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    actor_id = await _resolve_actor_member_id(current_user)
    return await admin_ops.resolve_report(
        db,
        report_id=report_id,
        actor_id=actor_id,
        new_status=body.status,
        assigned_to=body.assigned_to,
        resolution_note=body.resolution_note,
    )


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


@router.get("/audit", response_model=AuditLogPage)
async def admin_list_audit(
    channel_id: Optional[uuid.UUID] = Query(default=None),
    actor_id: Optional[uuid.UUID] = Query(default=None),
    subject_member_id: Optional[uuid.UUID] = Query(default=None),
    before_id: Optional[uuid.UUID] = Query(
        default=None,
        description=(
            "Cursor — pass the previous page's `next_before_id` for older entries."
        ),
    ),
    limit: int = Query(default=100, ge=1, le=500),
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    return await admin_ops.list_audit_log(
        db,
        channel_id=channel_id,
        actor_id=actor_id,
        subject_member_id=subject_member_id,
        before_id=before_id,
        limit=limit,
    )


# ---------------------------------------------------------------------------
# Safeguarding-admin only — minors-channel actions
# ---------------------------------------------------------------------------


@router.get("/safeguarding/health", tags=["safeguarding"])
async def safeguarding_health(
    current_user: AuthUser = Depends(require_safeguarding_admin),
):
    """Trivial endpoint that confirms a caller's safeguarding-admin role.

    Useful for the admin UI to decide whether to render the safeguarding
    panels at all (vs hiding them for plain admins)."""
    return {"safeguarding_admin": True, "user_id": current_user.user_id}
