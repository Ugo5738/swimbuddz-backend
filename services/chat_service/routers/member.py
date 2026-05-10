"""Member-facing chat endpoints.

All paths under `/chat/...`; the gateway proxies `/api/v1/chat/*`. Auth is the
Supabase JWT — we resolve auth_id → member_id at the boundary so chat tables
stay in members-service ID space (matches academy_service convention)."""

import uuid
from typing import Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import get_current_user
from libs.auth.models import AuthUser
from libs.common.logging import get_logger
from libs.common.service_client import get_member_by_auth_id
from libs.db.session import get_async_db

from services.chat_service.schemas import (
    DEFAULT_MESSAGE_PAGE_SIZE,
    MAX_MESSAGE_PAGE_SIZE,
    AttachmentUploadResponse,
    ChannelDetail,
    ChannelMarkReadRequest,
    ChannelMuteRequest,
    ChannelSummary,
    MessageEditRequest,
    MessageListPage,
    MessageOut,
    MessageSendRequest,
    ReactionAddRequest,
    ReportCreateRequest,
    ReportOut,
)
from services.chat_service.services import attachments, channel_ops, message_ops
from services.chat_service.services.permissions import (
    get_active_membership,
    get_channel_or_404,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])


_CALLING_SERVICE = "chat"


async def _resolve_member_id(current_user: AuthUser) -> uuid.UUID:
    """Resolve the caller's auth_id → members-service member_id (UUID).

    Chat membership is keyed on member_id, not auth_id, so every member-facing
    endpoint pays this round-trip. If the caller has no member profile yet,
    return 403 — they shouldn't be able to use chat without one."""
    member = await get_member_by_auth_id(
        current_user.user_id, calling_service=_CALLING_SERVICE
    )
    if member is None or "id" not in member:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Member profile not found",
        )
    return uuid.UUID(member["id"])


# ---------------------------------------------------------------------------
# Channels
# ---------------------------------------------------------------------------


@router.get("/channels", response_model=list[ChannelSummary])
async def list_my_channels(
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """List my active (non-archived) channels with unread counts and previews."""
    member_id = await _resolve_member_id(current_user)
    return await channel_ops.list_my_channels(db, member_id)


@router.get("/channels/{channel_id}", response_model=ChannelDetail)
async def get_channel(
    channel_id: uuid.UUID,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    member_id = await _resolve_member_id(current_user)
    channel = await get_channel_or_404(db, channel_id)
    membership = await get_active_membership(db, channel.id, member_id)
    return await channel_ops.get_channel_detail(db, channel, membership)


@router.post(
    "/channels/{channel_id}/read",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def mark_channel_read(
    channel_id: uuid.UUID,
    body: ChannelMarkReadRequest,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    member_id = await _resolve_member_id(current_user)
    channel = await get_channel_or_404(db, channel_id)
    membership = await get_active_membership(db, channel.id, member_id)
    await channel_ops.mark_read(db, membership, body.message_id)


@router.post(
    "/channels/{channel_id}/mute",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def mute_channel(
    channel_id: uuid.UUID,
    body: ChannelMuteRequest,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    member_id = await _resolve_member_id(current_user)
    channel = await get_channel_or_404(db, channel_id)
    membership = await get_active_membership(db, channel.id, member_id)
    await channel_ops.set_mute(db, membership, body.muted_until)


@router.post(
    "/channels/{channel_id}/leave",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def leave_channel(
    channel_id: uuid.UUID,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    member_id = await _resolve_member_id(current_user)
    channel = await get_channel_or_404(db, channel_id)
    membership = await get_active_membership(db, channel.id, member_id)
    await channel_ops.leave_channel(db, channel, membership)


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


@router.get("/channels/{channel_id}/messages", response_model=MessageListPage)
async def list_channel_messages(
    channel_id: uuid.UUID,
    before_id: Optional[uuid.UUID] = Query(
        default=None,
        description=(
            "Cursor — pass the previous page's `next_before_id` to fetch older "
            "messages. Omit for the newest page."
        ),
    ),
    limit: int = Query(
        default=DEFAULT_MESSAGE_PAGE_SIZE, ge=1, le=MAX_MESSAGE_PAGE_SIZE
    ),
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    member_id = await _resolve_member_id(current_user)
    channel = await get_channel_or_404(db, channel_id)
    await get_active_membership(db, channel.id, member_id)
    return await message_ops.list_messages(
        db,
        channel=channel,
        viewer_id=member_id,
        before_id=before_id,
        limit=limit,
    )


@router.post(
    "/channels/{channel_id}/messages",
    response_model=MessageOut,
    status_code=status.HTTP_201_CREATED,
)
async def send_channel_message(
    channel_id: uuid.UUID,
    body: MessageSendRequest,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Send a message. `client_message_id` is the idempotency key; reuse it
    on retry to avoid duplicates after a flaky network."""
    member_id = await _resolve_member_id(current_user)
    channel = await get_channel_or_404(db, channel_id)
    membership = await get_active_membership(db, channel.id, member_id)

    msg = await message_ops.send_message(
        db,
        channel=channel,
        membership=membership,
        body=body.body,
        attachments=body.attachments,
        reply_to_id=body.reply_to_id,
        client_message_id=body.client_message_id,
    )
    return await message_ops.get_message_with_reactions(
        db, message_id=msg.id, viewer_id=member_id
    )


@router.patch("/messages/{message_id}", response_model=MessageOut)
async def edit_message(
    message_id: uuid.UUID,
    body: MessageEditRequest,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    member_id = await _resolve_member_id(current_user)
    await message_ops.edit_message(
        db, message_id=message_id, editor_id=member_id, body=body.body
    )
    return await message_ops.get_message_with_reactions(
        db, message_id=message_id, viewer_id=member_id
    )


@router.delete(
    "/messages/{message_id}",
    response_model=MessageOut,
)
async def soft_delete_message(
    message_id: uuid.UUID,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Soft-delete (sender only). Body becomes `[deleted]`; row stays for audit."""
    member_id = await _resolve_member_id(current_user)
    await message_ops.soft_delete_own_message(
        db, message_id=message_id, deleter_id=member_id
    )
    return await message_ops.get_message_with_reactions(
        db, message_id=message_id, viewer_id=member_id
    )


# ---------------------------------------------------------------------------
# Reactions
# ---------------------------------------------------------------------------


@router.post(
    "/messages/{message_id}/reactions",
    response_model=MessageOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_reaction(
    message_id: uuid.UUID,
    body: ReactionAddRequest,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    member_id = await _resolve_member_id(current_user)
    await message_ops.add_reaction(
        db, message_id=message_id, member_id=member_id, emoji=body.emoji
    )
    return await message_ops.get_message_with_reactions(
        db, message_id=message_id, viewer_id=member_id
    )


@router.delete(
    "/messages/{message_id}/reactions/{emoji}",
    response_model=MessageOut,
)
async def remove_reaction(
    message_id: uuid.UUID,
    emoji: str,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    member_id = await _resolve_member_id(current_user)
    await message_ops.remove_reaction(
        db, message_id=message_id, member_id=member_id, emoji=emoji
    )
    return await message_ops.get_message_with_reactions(
        db, message_id=message_id, viewer_id=member_id
    )


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Attachments
# ---------------------------------------------------------------------------


@router.post(
    "/attachments",
    response_model=AttachmentUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_attachment(
    file: UploadFile = File(...),
    mime: Optional[str] = Form(
        default=None,
        description=(
            "Override the upload's content_type. Useful when the browser sends"
            " an octet-stream MIME for a recognised image."
        ),
    ),
    current_user: AuthUser = Depends(get_current_user),
):
    """Upload an image attachment. Returns a descriptor to embed in a message's
    `attachments` array, or `rejected=true` if pre-deliver moderation hit a
    safeguarding category we never deliver. Phase 1: images only."""
    member_id = await _resolve_member_id(current_user)
    declared_mime = (mime or file.content_type or "").lower()
    if not declared_mime:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot determine MIME type",
        )
    data = await file.read()
    return await attachments.upload_image_attachment(
        member_id=member_id,
        data=data,
        mime=declared_mime,
        declared_filename=file.filename,
    )


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


@router.post(
    "/messages/{message_id}/reports",
    response_model=ReportOut,
    status_code=status.HTTP_201_CREATED,
)
async def report_message(
    message_id: uuid.UUID,
    body: ReportCreateRequest,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """File a moderation report. Re-reporting the same message returns the
    existing open report rather than creating a duplicate."""
    member_id = await _resolve_member_id(current_user)
    return await message_ops.report_message(
        db,
        message_id=message_id,
        reporter_id=member_id,
        reason=body.reason,
        note=body.note,
    )
