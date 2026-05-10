"""Admin / moderator operations.

Hard-deletes, role changes, archive, report resolution, and audit-log queries.
Permissions are decided at the router (so the right `Depends(require_*)` is
applied per endpoint); this layer trusts that the caller is authorised and
focuses on the business rules.
"""

import uuid
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger

from services.chat_service.models import (
    ChannelMemberRole,
    ChatAuditAction,
    ChatAuditLog,
    ChatChannel,
    ChatChannelMember,
    ChatMessage,
    ChatMessageReport,
)
from services.chat_service.models.enums import ReportReason, ReportStatus
from services.chat_service.schemas import (
    AuditLogItem,
    AuditLogPage,
    ReportListItem,
)
from services.chat_service.services.audit_log import log_action

logger = get_logger(__name__)


_REPORT_PREVIEW_LENGTH = 120


def _preview(body: str, length: int = _REPORT_PREVIEW_LENGTH) -> str:
    if len(body) <= length:
        return body
    return body[: length - 1].rstrip() + "…"


def channel_has_minors(channel: ChatChannel) -> bool:
    """Whether the channel is flagged as containing minors.

    Set by the upstream service when the channel is created (e.g. academy
    knows the cohort age range). Default false."""
    flags = channel.safeguarding_flags or {}
    return bool(flags.get("has_minors", False))


# ---------------------------------------------------------------------------
# Hard delete
# ---------------------------------------------------------------------------


async def hard_delete_message(
    db: AsyncSession,
    *,
    message_id: uuid.UUID,
    actor_id: Optional[uuid.UUID],
    note: str,
) -> None:
    """Hard-delete a message. Caller must already be authorised — the safeguarding
    check on minor channels happens at the router boundary so the right
    `Depends(require_safeguarding_admin)` shows up in OpenAPI."""
    msg = await db.get(ChatMessage, message_id)
    if msg is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Message not found"
        )

    await log_action(
        db,
        action=ChatAuditAction.MESSAGE_DELETED,
        actor_id=actor_id,
        channel_id=msg.channel_id,
        message_id=msg.id,
        subject_member_id=msg.sender_id,
        payload={"hard": True, "note": note},
    )

    await db.delete(msg)
    await db.commit()


# ---------------------------------------------------------------------------
# Channel + member admin
# ---------------------------------------------------------------------------


async def archive_channel(
    db: AsyncSession,
    *,
    channel_id: uuid.UUID,
    actor_id: Optional[uuid.UUID],
) -> ChatChannel:
    """Soft-archive: `archived_at` set, channel becomes read-only via
    `require_channel_active`. Idempotent — re-archiving is a no-op."""
    channel = await db.get(ChatChannel, channel_id)
    if channel is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Channel not found"
        )
    if channel.archived_at is not None:
        return channel

    channel.archived_at = utc_now()
    await log_action(
        db,
        action=ChatAuditAction.CHANNEL_ARCHIVED,
        actor_id=actor_id,
        channel_id=channel.id,
    )
    await db.commit()
    await db.refresh(channel)
    return channel


async def update_member_role(
    db: AsyncSession,
    *,
    channel_id: uuid.UUID,
    member_id: uuid.UUID,
    new_role: ChannelMemberRole,
    actor_id: Optional[uuid.UUID],
) -> ChatChannelMember:
    result = await db.execute(
        select(ChatChannelMember).where(
            ChatChannelMember.channel_id == channel_id,
            ChatChannelMember.member_id == member_id,
            ChatChannelMember.left_at.is_(None),
        )
    )
    membership = result.scalar_one_or_none()
    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Membership not found",
        )
    if membership.role == new_role:
        return membership

    old_role = membership.role
    membership.role = new_role
    await log_action(
        db,
        action=ChatAuditAction.ROLE_CHANGED,
        actor_id=actor_id,
        channel_id=channel_id,
        subject_member_id=member_id,
        payload={"old_role": old_role.value, "new_role": new_role.value},
    )
    await db.commit()
    await db.refresh(membership)
    return membership


# ---------------------------------------------------------------------------
# Reports queue
# ---------------------------------------------------------------------------


async def list_reports(
    db: AsyncSession,
    *,
    status_filter: Optional[ReportStatus],
    reason_filter: Optional[ReportReason],
    assigned_to: Optional[uuid.UUID],
    skip: int,
    limit: int,
) -> list[ReportListItem]:
    """Moderator queue. Default ordering puts open reports oldest-first so the
    backlog drains FIFO."""
    base = select(ChatMessageReport)
    if status_filter is not None:
        base = base.where(ChatMessageReport.status == status_filter)
    if reason_filter is not None:
        base = base.where(ChatMessageReport.reason == reason_filter)
    if assigned_to is not None:
        base = base.where(ChatMessageReport.assigned_to == assigned_to)
    base = base.order_by(ChatMessageReport.created_at.asc()).offset(skip).limit(limit)
    rows = (await db.execute(base)).scalars().all()

    if not rows:
        return []

    msg_ids = {r.message_id for r in rows}
    msg_rows = (
        (await db.execute(select(ChatMessage).where(ChatMessage.id.in_(msg_ids))))
        .scalars()
        .all()
    )
    by_msg = {m.id: m for m in msg_rows}

    items: list[ReportListItem] = []
    for r in rows:
        msg = by_msg.get(r.message_id)
        items.append(
            ReportListItem(
                id=r.id,
                message_id=r.message_id,
                reporter_id=r.reporter_id,
                reason=r.reason,
                note=r.note,
                status=r.status,
                assigned_to=r.assigned_to,
                resolved_at=r.resolved_at,
                resolution_note=r.resolution_note,
                created_at=r.created_at,
                channel_id=msg.channel_id if msg else None,
                sender_id=msg.sender_id if msg else None,
                body_preview=_preview(msg.body) if msg else None,
            )
        )
    return items


async def resolve_report(
    db: AsyncSession,
    *,
    report_id: uuid.UUID,
    actor_id: Optional[uuid.UUID],
    new_status: Optional[ReportStatus],
    assigned_to: Optional[uuid.UUID],
    resolution_note: Optional[str],
) -> ChatMessageReport:
    report = await db.get(ChatMessageReport, report_id)
    if report is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Report not found"
        )

    changed_fields: dict = {}

    if assigned_to is not None and report.assigned_to != assigned_to:
        changed_fields["old_assigned_to"] = (
            str(report.assigned_to) if report.assigned_to else None
        )
        changed_fields["new_assigned_to"] = str(assigned_to)
        report.assigned_to = assigned_to

    if new_status is not None and report.status != new_status:
        changed_fields["old_status"] = report.status.value
        changed_fields["new_status"] = new_status.value
        report.status = new_status
        if new_status in (ReportStatus.RESOLVED, ReportStatus.DISMISSED):
            report.resolved_at = utc_now()
            if resolution_note is not None:
                report.resolution_note = resolution_note

    if not changed_fields and resolution_note is not None:
        report.resolution_note = resolution_note
        changed_fields["resolution_note_updated"] = True

    if changed_fields:
        await log_action(
            db,
            action=ChatAuditAction.REPORT_RESOLVED,
            actor_id=actor_id,
            message_id=report.message_id,
            payload=changed_fields,
        )

    await db.commit()
    await db.refresh(report)
    return report


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


async def list_audit_log(
    db: AsyncSession,
    *,
    channel_id: Optional[uuid.UUID],
    actor_id: Optional[uuid.UUID],
    subject_member_id: Optional[uuid.UUID],
    before_id: Optional[uuid.UUID],
    limit: int,
) -> AuditLogPage:
    """Cursor-paginated, newest-first audit log slice."""
    base = select(ChatAuditLog)
    if channel_id is not None:
        base = base.where(ChatAuditLog.channel_id == channel_id)
    if actor_id is not None:
        base = base.where(ChatAuditLog.actor_id == actor_id)
    if subject_member_id is not None:
        base = base.where(ChatAuditLog.subject_member_id == subject_member_id)

    if before_id is not None:
        cursor = await db.get(ChatAuditLog, before_id)
        if cursor is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cursor entry not found",
            )
        base = base.where(
            (ChatAuditLog.created_at < cursor.created_at)
            | (
                (ChatAuditLog.created_at == cursor.created_at)
                & (ChatAuditLog.id < cursor.id)
            )
        )

    base = base.order_by(desc(ChatAuditLog.created_at), desc(ChatAuditLog.id)).limit(
        limit + 1
    )
    rows = (await db.execute(base)).scalars().all()
    has_more = len(rows) > limit
    if has_more:
        rows = rows[:limit]

    items = [AuditLogItem.model_validate(r) for r in rows]
    next_before_id = rows[-1].id if has_more else None
    return AuditLogPage(items=items, next_before_id=next_before_id, has_more=has_more)
