"""Membership reconciliation — add/remove members from a channel.

Called by the internal s2s endpoint when an upstream service emits an event
("enrollment.confirmed" → add to cohort channel, "rsvp.cancelled" → remove
from event channel, etc.). See design §4.2.
"""

import uuid
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.datetime_utils import utc_now

from services.chat_service.models import (
    ChannelMemberRole,
    ChatAuditAction,
    ChatChannel,
    ChatChannelMember,
    MembershipDerivation,
    ParentEntityType,
)
from services.chat_service.services.audit_log import log_action


async def resolve_channel(
    db: AsyncSession,
    *,
    channel_id: Optional[uuid.UUID],
    parent_entity_type: Optional[str],
    parent_entity_id: Optional[uuid.UUID],
) -> ChatChannel:
    if channel_id is not None:
        ch = await db.get(ChatChannel, channel_id)
        if ch is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Channel not found",
            )
        return ch

    if parent_entity_type is None or parent_entity_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Either channel_id or parent_entity_type+parent_entity_id required",
        )

    # Validate the enum string up-front so the response is precise.
    try:
        pet = ParentEntityType(parent_entity_type)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown parent_entity_type: {parent_entity_type}",
        ) from e

    result = await db.execute(
        select(ChatChannel).where(
            ChatChannel.parent_entity_type == pet,
            ChatChannel.parent_entity_id == parent_entity_id,
        )
    )
    ch = result.scalar_one_or_none()
    if ch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"No channel found for parent {parent_entity_type}/{parent_entity_id}"
            ),
        )
    return ch


async def _get_membership(
    db: AsyncSession, channel_id: uuid.UUID, member_id: uuid.UUID
) -> Optional[ChatChannelMember]:
    result = await db.execute(
        select(ChatChannelMember).where(
            ChatChannelMember.channel_id == channel_id,
            ChatChannelMember.member_id == member_id,
        )
    )
    return result.scalar_one_or_none()


async def add_member(
    db: AsyncSession,
    *,
    channel: ChatChannel,
    member_id: uuid.UUID,
    role: ChannelMemberRole = ChannelMemberRole.MEMBER,
    derived_from: MembershipDerivation = MembershipDerivation.MANUAL,
    derivation_ref: Optional[uuid.UUID] = None,
    actor_id: Optional[uuid.UUID] = None,
) -> ChatChannelMember:
    """Add (or re-activate) a member in a channel.

    If the member previously left (`left_at` set), this re-activates the row
    rather than inserting a duplicate. Idempotent for active members.
    """
    existing = await _get_membership(db, channel.id, member_id)

    if existing is not None and existing.left_at is None:
        # Already an active member — no-op, but allow role/derivation upgrade.
        changed = False
        if existing.role != role:
            existing.role = role
            changed = True
        if existing.derived_from != derived_from:
            existing.derived_from = derived_from
            changed = True
        if existing.derivation_ref != derivation_ref:
            existing.derivation_ref = derivation_ref
            changed = True
        if changed:
            await db.flush()
            await db.commit()
            await db.refresh(existing)
        return existing

    if existing is not None:
        # Re-activate after a previous leave.
        existing.left_at = None
        existing.role = role
        existing.derived_from = derived_from
        existing.derivation_ref = derivation_ref
        existing.joined_at = utc_now()
        await db.flush()
    else:
        existing = ChatChannelMember(
            channel_id=channel.id,
            member_id=member_id,
            role=role,
            derived_from=derived_from,
            derivation_ref=derivation_ref,
        )
        db.add(existing)
        await db.flush()

    await log_action(
        db,
        action=ChatAuditAction.MEMBER_ADDED,
        actor_id=actor_id,
        channel_id=channel.id,
        subject_member_id=member_id,
        payload={
            "role": role.value,
            "derived_from": derived_from.value,
            "derivation_ref": str(derivation_ref) if derivation_ref else None,
        },
    )
    await db.commit()
    await db.refresh(existing)
    return existing


async def remove_member(
    db: AsyncSession,
    *,
    channel: ChatChannel,
    member_id: uuid.UUID,
    actor_id: Optional[uuid.UUID] = None,
) -> None:
    """Soft-remove a member (sets `left_at`). No-op if already left or never
    a member."""
    existing = await _get_membership(db, channel.id, member_id)
    if existing is None or existing.left_at is not None:
        return
    existing.left_at = utc_now()
    await log_action(
        db,
        action=ChatAuditAction.MEMBER_REMOVED,
        actor_id=actor_id,
        channel_id=channel.id,
        subject_member_id=member_id,
    )
    await db.commit()
