"""Append-only chat audit log helper.

Every significant chat event flows through here so the audit trail stays in
one place. Callers pass the open AsyncSession — this function flushes but
never commits, so the audit row participates in the caller's transaction
(if the message-send rolls back, the audit row rolls back with it)."""

import uuid
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from services.chat_service.models import ChatAuditAction, ChatAuditLog


async def log_action(
    db: AsyncSession,
    *,
    action: ChatAuditAction,
    actor_id: Optional[uuid.UUID] = None,
    channel_id: Optional[uuid.UUID] = None,
    message_id: Optional[uuid.UUID] = None,
    subject_member_id: Optional[uuid.UUID] = None,
    payload: Optional[dict] = None,
) -> ChatAuditLog:
    entry = ChatAuditLog(
        actor_id=actor_id,
        action=action,
        channel_id=channel_id,
        message_id=message_id,
        subject_member_id=subject_member_id,
        payload=payload or {},
    )
    db.add(entry)
    await db.flush()
    return entry
