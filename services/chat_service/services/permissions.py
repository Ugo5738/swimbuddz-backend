"""Permission helpers used by chat endpoints.

Permissions are enforced at the API boundary on every write — see design §5.3.
These helpers raise HTTPException directly so routers stay declarative.
"""

import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.chat_service.models import (
    ChannelMemberRole,
    ChatChannel,
    ChatChannelMember,
)


async def get_channel_or_404(db: AsyncSession, channel_id: uuid.UUID) -> ChatChannel:
    channel = await db.get(ChatChannel, channel_id)
    if channel is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Channel not found"
        )
    return channel


async def get_active_membership(
    db: AsyncSession, channel_id: uuid.UUID, member_id: uuid.UUID
) -> ChatChannelMember:
    """Return the caller's active (left_at IS NULL) membership row, or 403.

    Used as the gate for any per-channel read or write."""
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
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not a member of this channel",
        )
    return membership


def require_channel_active(channel: ChatChannel) -> None:
    """Refuse writes (send, react, report) on archived channels."""
    if channel.archived_at is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Channel is archived",
        )


def require_can_post(channel: ChatChannel, membership: ChatChannelMember) -> None:
    """Posting rules per design §3 + §5.1.

    - Observers never post (used for muted/safeguarding-observer states).
    - In broadcast channels only moderators/admins post.
    """
    require_channel_active(channel)
    if membership.role == ChannelMemberRole.OBSERVER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Observers cannot post in this channel",
        )
    if channel.type.value == "broadcast" and membership.role not in (
        ChannelMemberRole.MODERATOR,
        ChannelMemberRole.ADMIN,
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only moderators or admins can post in broadcast channels",
        )
