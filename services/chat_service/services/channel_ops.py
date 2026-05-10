"""Channel-level operations: list, fetch, ensure-by-parent, mark-read, mute, leave."""

import uuid
from datetime import datetime
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import and_, desc, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger

from services.chat_service.models import (
    ChannelMemberRole,
    ChannelType,
    ChatAuditAction,
    ChatChannel,
    ChatChannelMember,
    ChatMessage,
    MembershipDerivation,
    ParentEntityType,
    RetentionPolicy,
)
from services.chat_service.schemas import (
    ChannelDetail,
    ChannelSummary,
    LastMessagePreview,
)
from services.chat_service.services.audit_log import log_action

logger = get_logger(__name__)

# Body preview length on channel-list rows. Short — the channel list is a
# scrollable directory, not a reading surface.
_PREVIEW_LENGTH = 80


def _preview_body(body: str) -> str:
    if len(body) <= _PREVIEW_LENGTH:
        return body
    return body[: _PREVIEW_LENGTH - 1].rstrip() + "…"


async def _last_message(
    db: AsyncSession, channel_id: uuid.UUID
) -> Optional[ChatMessage]:
    """Most recent non-deleted message in the channel, or None."""
    result = await db.execute(
        select(ChatMessage)
        .where(
            ChatMessage.channel_id == channel_id,
            ChatMessage.deleted_at.is_(None),
        )
        .order_by(desc(ChatMessage.created_at))
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _unread_count(
    db: AsyncSession,
    channel_id: uuid.UUID,
    last_read_message_id: Optional[uuid.UUID],
) -> int:
    """Messages newer than last_read_message_id (exclusive)."""
    base = (
        select(func.count())
        .select_from(ChatMessage)
        .where(
            ChatMessage.channel_id == channel_id,
            ChatMessage.deleted_at.is_(None),
        )
    )
    if last_read_message_id is not None:
        # Use the read message's created_at as the cutoff. Falls through to
        # "all messages" if the referenced row was hard-deleted.
        cutoff = await db.execute(
            select(ChatMessage.created_at).where(ChatMessage.id == last_read_message_id)
        )
        cutoff_at = cutoff.scalar_one_or_none()
        if cutoff_at is not None:
            base = base.where(ChatMessage.created_at > cutoff_at)
    result = await db.execute(base)
    return int(result.scalar() or 0)


async def list_my_channels(
    db: AsyncSession, member_id: uuid.UUID
) -> list[ChannelSummary]:
    """All non-archived channels the caller is currently a member of."""
    result = await db.execute(
        select(ChatChannel, ChatChannelMember)
        .join(
            ChatChannelMember,
            and_(
                ChatChannelMember.channel_id == ChatChannel.id,
                ChatChannelMember.member_id == member_id,
                ChatChannelMember.left_at.is_(None),
            ),
        )
        .where(ChatChannel.archived_at.is_(None))
        .order_by(desc(ChatChannel.created_at))
    )
    rows = result.all()

    summaries: list[ChannelSummary] = []
    for channel, membership in rows:
        last_msg = await _last_message(db, channel.id)
        last_preview = (
            LastMessagePreview(
                id=last_msg.id,
                sender_id=last_msg.sender_id,
                body_preview=_preview_body(last_msg.body),
                created_at=last_msg.created_at,
            )
            if last_msg is not None
            else None
        )
        unread = await _unread_count(db, channel.id, membership.last_read_message_id)
        summaries.append(
            ChannelSummary(
                id=channel.id,
                type=channel.type,
                parent_entity_type=channel.parent_entity_type,
                parent_entity_id=channel.parent_entity_id,
                name=channel.name,
                archived_at=channel.archived_at,
                created_at=channel.created_at,
                my_role=membership.role,
                my_muted_until=membership.muted_until,
                my_last_read_message_id=membership.last_read_message_id,
                unread_count=unread,
                last_message=last_preview,
            )
        )
    return summaries


async def get_channel_detail(
    db: AsyncSession,
    channel: ChatChannel,
    membership: Optional[ChatChannelMember] = None,
) -> ChannelDetail:
    """Project a channel into the detail view. `membership` is the caller's
    own row — pass None when the caller is an admin viewing the channel
    without joining (the per-caller fields fall back to safe defaults)."""
    last_msg = await _last_message(db, channel.id)
    last_preview = (
        LastMessagePreview(
            id=last_msg.id,
            sender_id=last_msg.sender_id,
            body_preview=_preview_body(last_msg.body),
            created_at=last_msg.created_at,
        )
        if last_msg is not None
        else None
    )
    unread = (
        await _unread_count(db, channel.id, membership.last_read_message_id)
        if membership is not None
        else 0
    )

    member_count_result = await db.execute(
        select(func.count())
        .select_from(ChatChannelMember)
        .where(
            ChatChannelMember.channel_id == channel.id,
            ChatChannelMember.left_at.is_(None),
        )
    )
    member_count = int(member_count_result.scalar() or 0)

    # Default `my_role` to OBSERVER for non-member admin viewers — honest
    # representation: they're looking but not posting from this view.
    my_role = membership.role if membership is not None else ChannelMemberRole.OBSERVER
    my_muted_until = membership.muted_until if membership is not None else None
    my_last_read = membership.last_read_message_id if membership is not None else None

    return ChannelDetail(
        id=channel.id,
        type=channel.type,
        parent_entity_type=channel.parent_entity_type,
        parent_entity_id=channel.parent_entity_id,
        name=channel.name,
        archived_at=channel.archived_at,
        created_at=channel.created_at,
        description=channel.description,
        retention_policy=channel.retention_policy,
        created_by=channel.created_by,
        safeguarding_flags=channel.safeguarding_flags or {},
        member_count=member_count,
        my_role=my_role,
        my_muted_until=my_muted_until,
        my_last_read_message_id=my_last_read,
        unread_count=unread,
        last_message=last_preview,
    )


async def mark_read(
    db: AsyncSession,
    membership: ChatChannelMember,
    message_id: uuid.UUID,
) -> ChatChannelMember:
    """Move the caller's last_read pointer forward (never backward).

    Only accepts message IDs that belong to this channel — prevents callers
    from poisoning their own pointer with someone else's id.
    """
    msg = await db.get(ChatMessage, message_id)
    if msg is None or msg.channel_id != membership.channel_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Message does not belong to this channel",
        )

    if membership.last_read_message_id is not None:
        current = await db.get(ChatMessage, membership.last_read_message_id)
        if current is not None and msg.created_at < current.created_at:
            # Already read past this point — no-op, don't move backward.
            return membership

    membership.last_read_message_id = message_id
    await db.flush()
    await db.commit()
    await db.refresh(membership)
    return membership


async def set_mute(
    db: AsyncSession,
    membership: ChatChannelMember,
    muted_until: Optional[datetime],
) -> ChatChannelMember:
    membership.muted_until = muted_until
    await db.flush()
    await db.commit()
    await db.refresh(membership)
    return membership


async def leave_channel(
    db: AsyncSession,
    channel: ChatChannel,
    membership: ChatChannelMember,
) -> None:
    """Soft-leave: row stays for audit, only `left_at` is set.

    Derived memberships (enrollment, RSVP, …) cannot be left manually — leaving
    those would just be re-added by the next reconciliation pass and confuse
    the user. Refuse and tell the caller to drop the upstream subscription
    instead.
    """
    if membership.derived_from != MembershipDerivation.MANUAL:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "This channel's membership is derived from your enrollment / "
                "RSVP / pod assignment — leave the parent to leave the channel."
            ),
        )
    membership.left_at = utc_now()
    await log_action(
        db,
        action=ChatAuditAction.CHANNEL_LEFT,
        actor_id=membership.member_id,
        channel_id=channel.id,
        subject_member_id=membership.member_id,
    )
    await db.commit()


async def ensure_channel(
    db: AsyncSession,
    *,
    type: ChannelType,
    parent_entity_type: ParentEntityType,
    parent_entity_id: Optional[uuid.UUID],
    name: str,
    retention_policy: RetentionPolicy,
    description: Optional[str] = None,
    created_by: Optional[uuid.UUID] = None,
    safeguarding_flags: Optional[dict] = None,
) -> tuple[ChatChannel, bool]:
    """Idempotent create-or-fetch keyed on (type, parent_entity_type, parent_entity_id).

    Returns (channel, created) — `created` is True only on the first call.
    The unique key is logical, not enforced by a DB constraint, because
    parent_entity_id is nullable for ad-hoc channels (NONE parent type).
    For NONE-parent channels we always create a new row — they're admin-
    created ad-hoc channels and don't dedupe on a key.
    """
    if parent_entity_type != ParentEntityType.NONE and parent_entity_id is not None:
        existing_q = select(ChatChannel).where(
            ChatChannel.type == type,
            ChatChannel.parent_entity_type == parent_entity_type,
            ChatChannel.parent_entity_id == parent_entity_id,
        )
        result = await db.execute(existing_q)
        existing = result.scalar_one_or_none()
        if existing is not None:
            return existing, False

    channel = ChatChannel(
        type=type,
        parent_entity_type=parent_entity_type,
        parent_entity_id=parent_entity_id,
        name=name,
        description=description,
        retention_policy=retention_policy,
        created_by=created_by,
        safeguarding_flags=safeguarding_flags or {},
    )
    db.add(channel)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        # Another caller raced us — re-fetch the winner.
        if parent_entity_type != ParentEntityType.NONE and parent_entity_id is not None:
            result = await db.execute(
                select(ChatChannel).where(
                    ChatChannel.type == type,
                    ChatChannel.parent_entity_type == parent_entity_type,
                    ChatChannel.parent_entity_id == parent_entity_id,
                )
            )
            winner = result.scalar_one_or_none()
            if winner is not None:
                return winner, False
        raise

    if created_by is not None:
        # Creator joins as admin so they can manage the channel from the start.
        creator_membership = ChatChannelMember(
            channel_id=channel.id,
            member_id=created_by,
            role=ChannelMemberRole.ADMIN,
            derived_from=MembershipDerivation.MANUAL,
        )
        db.add(creator_membership)
        await db.flush()

    await db.commit()
    await db.refresh(channel)
    logger.info(
        "Created chat channel %s (type=%s, parent=%s/%s)",
        channel.id,
        type.value,
        parent_entity_type.value,
        parent_entity_id,
    )
    return channel, True
