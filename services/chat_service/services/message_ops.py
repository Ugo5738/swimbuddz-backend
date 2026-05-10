"""Message-level operations: list, send, edit, soft-delete, react, report.

Idempotency note: client-supplied `client_message_id` becomes the row's
primary key, so concurrent retries of the same logical send converge on a
single row (the second insert hits a unique-key violation, which we resolve
by returning the existing row).
"""

import uuid
from collections import defaultdict
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import asc, desc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from libs.common.service_client import dispatch_notification
from libs.moderation import (
    ModerationResult,
    ProviderUnavailableError,
    moderate_text,
)

from services.chat_service.models import (
    ChatAuditAction,
    ChatChannel,
    ChatChannelMember,
    ChatMessage,
    ChatMessageReaction,
    ChatMessageReport,
)
from services.chat_service.models.enums import (
    ReportReason,
    ReportStatus,
    SafeguardingReviewState,
)
from services.chat_service.schemas import (
    ALLOWED_REACTION_EMOJI,
    AttachmentDescriptor,
    MessageListPage,
    MessageOut,
    ReactionSummary,
)
from services.chat_service.services.audit_log import log_action
from services.chat_service.services.permissions import (
    get_active_membership,
    require_can_post,
)

logger = get_logger(__name__)

# Replacement body shown to clients for soft-deleted messages.
_DELETED_PLACEHOLDER = "[deleted]"

# Default flag threshold for OpenAI Moderation. Tunable per channel later
# (design §6.1 rule 6 — thresholds must be configurable, not hard-coded
# verdicts). Lower for channels with minors via an override mechanism we
# add when the safeguarding admin tooling lands.
_DEFAULT_TEXT_FLAG_THRESHOLD = 0.5


def _channel_has_minors(channel: ChatChannel) -> bool:
    return bool((channel.safeguarding_flags or {}).get("has_minors", False))


def _validate_attachments_for_channel(channel: ChatChannel, attachments: list) -> bool:
    """Apply the per-channel attachment policy.

    Returns True if the message itself should be marked FLAGGED (i.e. an
    attachment was flagged but allowed through to the safeguarding queue);
    False otherwise. Raises 422 to refuse the send when a flagged image
    lands in a minor channel.

    Hard-rejects (`SAFEGUARDING` category) are caught at upload time and
    never reach `send_message` — the descriptor wouldn't exist."""
    if not attachments:
        return False

    flagged_any = False
    for raw in attachments:
        # Defensive parse: clients send arbitrary JSON in the array; we only
        # gate on what we recognise as our own descriptor shape.
        try:
            descriptor = AttachmentDescriptor.model_validate(raw)
        except Exception:
            continue
        if descriptor.moderation is None or not descriptor.moderation.flagged:
            continue
        flagged_any = True
        if _channel_has_minors(channel):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "Attachment flagged by image moderation; cannot be posted"
                    " in a channel containing minors."
                ),
            )
    return flagged_any


async def _check_text_moderation(body: str) -> SafeguardingReviewState:
    """Run text moderation pre-persist and return the review state to record.

    Policy (Phase 1):
      - Provider unavailable → return NONE (open-by-default; covers local
        dev where OPENAI_API_KEY is unset). Will switch to fail-closed for
        channels with `has_minors=true` once the override config lands.
      - Provider returns flagged=true → return FLAGGED so the message
        surfaces in the safeguarding admin queue. Message is still
        delivered — design rule: "Never auto-delete." Quarantine workflow
        comes when the admin moderation UI ships.
      - Provider returns flagged=false → return NONE.
    """
    try:
        result: ModerationResult = await moderate_text(
            body, flag_threshold=_DEFAULT_TEXT_FLAG_THRESHOLD
        )
    except ProviderUnavailableError as exc:
        logger.warning("Text moderation skipped (provider unavailable): %s", exc)
        return SafeguardingReviewState.NONE

    if result.flagged:
        top = result.top_label()
        logger.info(
            "Message flagged by text moderation: top=%s confidence=%.3f",
            top.category.value if top else "?",
            top.confidence if top else 0.0,
        )
        return SafeguardingReviewState.FLAGGED
    return SafeguardingReviewState.NONE


# Body preview length on push notifications. Slightly larger than the channel
# preview because the notification surface is the only place a non-active user
# sees the content.
_NOTIFICATION_PREVIEW_LENGTH = 140


def _notification_preview(body: str) -> str:
    if len(body) <= _NOTIFICATION_PREVIEW_LENGTH:
        return body
    return body[: _NOTIFICATION_PREVIEW_LENGTH - 1].rstrip() + "…"


async def _dispatch_new_message_notification(
    db: AsyncSession,
    *,
    channel: ChatChannel,
    message: ChatMessage,
) -> None:
    """Fan out a "new message" notification to every active, non-muted
    channel member except the sender.

    Best-effort — `dispatch_notification` is itself fire-and-forget on errors.
    Empty recipient list is a no-op (e.g. a one-member channel)."""
    now = utc_now()
    result = await db.execute(
        select(ChatChannelMember.member_id).where(
            ChatChannelMember.channel_id == channel.id,
            ChatChannelMember.left_at.is_(None),
            ChatChannelMember.member_id != message.sender_id,
            (
                (ChatChannelMember.muted_until.is_(None))
                | (ChatChannelMember.muted_until <= now)
            ),
        )
    )
    recipients = [str(row[0]) for row in result.all()]
    if not recipients:
        return

    await dispatch_notification(
        type="chat_message",
        category="chat",
        member_ids=recipients,
        title=f"New message in {channel.name}",
        body=_notification_preview(message.body),
        action_url=f"/account/chat/{channel.id}",
        icon="message-circle",
        metadata={
            "channel_id": str(channel.id),
            "message_id": str(message.id),
            "sender_id": str(message.sender_id),
        },
        calling_service="chat",
    )


def _to_message_out(
    msg: ChatMessage, viewer_id: uuid.UUID, reactions: list[ChatMessageReaction]
) -> MessageOut:
    """Project a ChatMessage row + its reactions into the wire shape.

    Reactions are aggregated server-side (one row per emoji, with the count
    and a `reacted_by_me` flag) so clients never need the per-member rows.
    """
    by_emoji: dict[str, list[ChatMessageReaction]] = defaultdict(list)
    for r in reactions:
        by_emoji[r.emoji].append(r)
    summary = [
        ReactionSummary(
            emoji=emoji,
            count=len(rs),
            reacted_by_me=any(r.member_id == viewer_id for r in rs),
        )
        for emoji, rs in sorted(by_emoji.items())
    ]

    body = _DELETED_PLACEHOLDER if msg.deleted_at is not None else msg.body
    return MessageOut(
        id=msg.id,
        channel_id=msg.channel_id,
        sender_id=msg.sender_id,
        body=body,
        attachments=msg.attachments or [],
        reply_to_id=msg.reply_to_id,
        created_at=msg.created_at,
        edited_at=msg.edited_at,
        deleted_at=msg.deleted_at,
        reactions=summary,
    )


async def list_messages(
    db: AsyncSession,
    *,
    channel: ChatChannel,
    viewer_id: uuid.UUID,
    before_id: Optional[uuid.UUID],
    limit: int,
) -> MessageListPage:
    """Newest-first cursor page of messages in a channel.

    `before_id` is the cursor — pass the previous page's `next_before_id` to
    fetch older messages. We over-fetch by one to detect `has_more` cheaply.
    """
    base = select(ChatMessage).where(ChatMessage.channel_id == channel.id)

    if before_id is not None:
        cursor_msg = await db.get(ChatMessage, before_id)
        if cursor_msg is None or cursor_msg.channel_id != channel.id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cursor message does not belong to this channel",
            )
        # Use (created_at, id) as the cursor tuple to break ties deterministically.
        base = base.where(
            (ChatMessage.created_at < cursor_msg.created_at)
            | (
                (ChatMessage.created_at == cursor_msg.created_at)
                & (ChatMessage.id < cursor_msg.id)
            )
        )

    base = base.order_by(desc(ChatMessage.created_at), desc(ChatMessage.id)).limit(
        limit + 1
    )
    rows = (await db.execute(base)).scalars().all()
    has_more = len(rows) > limit
    if has_more:
        rows = rows[:limit]

    if not rows:
        return MessageListPage(items=[], next_before_id=None, has_more=False)

    msg_ids = [m.id for m in rows]
    reactions_by_msg: dict[uuid.UUID, list[ChatMessageReaction]] = defaultdict(list)
    if msg_ids:
        rxn_rows = (
            (
                await db.execute(
                    select(ChatMessageReaction).where(
                        ChatMessageReaction.message_id.in_(msg_ids)
                    )
                )
            )
            .scalars()
            .all()
        )
        for r in rxn_rows:
            reactions_by_msg[r.message_id].append(r)

    items = [
        _to_message_out(m, viewer_id, reactions_by_msg.get(m.id, [])) for m in rows
    ]
    next_before_id = rows[-1].id if has_more else None
    return MessageListPage(
        items=items, next_before_id=next_before_id, has_more=has_more
    )


async def send_message(
    db: AsyncSession,
    *,
    channel: ChatChannel,
    membership: ChatChannelMember,
    body: str,
    attachments: list,
    reply_to_id: Optional[uuid.UUID],
    client_message_id: uuid.UUID,
) -> ChatMessage:
    """Create a new message. Caller must already hold an active membership;
    permissions are enforced here too as a defence-in-depth check."""
    require_can_post(channel, membership)

    if reply_to_id is not None:
        parent = await db.get(ChatMessage, reply_to_id)
        if parent is None or parent.channel_id != channel.id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="reply_to_id must reference a message in the same channel",
            )

    # Idempotent: the client-generated UUID is the row's primary key. A retry
    # of the same logical send re-uses the same id and we return the winning row.
    existing = await db.get(ChatMessage, client_message_id)
    if existing is not None:
        if existing.sender_id != membership.member_id:
            # Someone else owns this id — refuse to clobber.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="client_message_id already in use",
            )
        return existing

    review_state = await _check_text_moderation(body)
    # Attachment policy may upgrade the review state to FLAGGED, or refuse
    # the send entirely (in minor channels with a flagged image).
    if _validate_attachments_for_channel(channel, attachments or []):
        review_state = SafeguardingReviewState.FLAGGED

    msg = ChatMessage(
        id=client_message_id,
        channel_id=channel.id,
        sender_id=membership.member_id,
        body=body,
        attachments=attachments or [],
        reply_to_id=reply_to_id,
        safeguarding_review_state=review_state,
    )
    db.add(msg)

    await log_action(
        db,
        action=ChatAuditAction.MESSAGE_SENT,
        actor_id=membership.member_id,
        channel_id=channel.id,
        message_id=msg.id,
        payload=(
            {"safeguarding_review_state": review_state.value}
            if review_state != SafeguardingReviewState.NONE
            else None
        ),
    )

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        # Concurrent retry won the race — return the winner so the caller still
        # sees a successful idempotent response.
        winner = await db.get(ChatMessage, client_message_id)
        if winner is not None:
            return winner
        raise

    await db.refresh(msg)

    # Best-effort fan-out — never blocks or fails the send.
    try:
        await _dispatch_new_message_notification(db, channel=channel, message=msg)
    except Exception:
        logger.warning(
            "chat notification fan-out failed for message=%s (best-effort, continuing)",
            msg.id,
            exc_info=True,
        )
    return msg


async def edit_message(
    db: AsyncSession,
    *,
    message_id: uuid.UUID,
    editor_id: uuid.UUID,
    body: str,
) -> ChatMessage:
    """Edit own message. Refuses if soft-deleted or not the sender."""
    msg = await db.get(ChatMessage, message_id)
    if msg is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Message not found"
        )
    if msg.sender_id != editor_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only edit your own messages",
        )
    if msg.deleted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot edit a deleted message",
        )

    # Caller still has to be in the channel — protects against editing after
    # being removed.
    channel = await db.get(ChatChannel, msg.channel_id)
    if channel is not None:
        await get_active_membership(db, channel.id, editor_id)

    msg.body = body
    msg.edited_at = utc_now()

    await log_action(
        db,
        action=ChatAuditAction.MESSAGE_EDITED,
        actor_id=editor_id,
        channel_id=msg.channel_id,
        message_id=msg.id,
    )
    await db.commit()
    await db.refresh(msg)
    return msg


async def soft_delete_own_message(
    db: AsyncSession,
    *,
    message_id: uuid.UUID,
    deleter_id: uuid.UUID,
) -> ChatMessage:
    """Sender soft-deletes their own message. Hard-delete is restricted to
    safeguarding admins (see design §6.1 rule 5) and lives on the admin router."""
    msg = await db.get(ChatMessage, message_id)
    if msg is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Message not found"
        )
    if msg.sender_id != deleter_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only delete your own messages",
        )
    if msg.deleted_at is not None:
        return msg  # idempotent

    msg.deleted_at = utc_now()
    msg.deleted_by = deleter_id

    await log_action(
        db,
        action=ChatAuditAction.MESSAGE_DELETED,
        actor_id=deleter_id,
        channel_id=msg.channel_id,
        message_id=msg.id,
        payload={"hard": False, "by_sender": True},
    )
    await db.commit()
    await db.refresh(msg)
    return msg


async def add_reaction(
    db: AsyncSession,
    *,
    message_id: uuid.UUID,
    member_id: uuid.UUID,
    emoji: str,
) -> ChatMessageReaction:
    if emoji not in ALLOWED_REACTION_EMOJI:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Reaction emoji not allowed. Allowed: {sorted(ALLOWED_REACTION_EMOJI)}",
        )
    msg = await db.get(ChatMessage, message_id)
    if msg is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Message not found"
        )
    if msg.deleted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot react to a deleted message",
        )

    # Reactor must be an active member of the channel.
    await get_active_membership(db, msg.channel_id, member_id)

    rxn = ChatMessageReaction(message_id=message_id, member_id=member_id, emoji=emoji)
    db.add(rxn)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        # Already reacted with this emoji — return the existing row.
        existing = await db.execute(
            select(ChatMessageReaction).where(
                ChatMessageReaction.message_id == message_id,
                ChatMessageReaction.member_id == member_id,
                ChatMessageReaction.emoji == emoji,
            )
        )
        return existing.scalar_one()
    await db.refresh(rxn)
    return rxn


async def remove_reaction(
    db: AsyncSession,
    *,
    message_id: uuid.UUID,
    member_id: uuid.UUID,
    emoji: str,
) -> None:
    result = await db.execute(
        select(ChatMessageReaction).where(
            ChatMessageReaction.message_id == message_id,
            ChatMessageReaction.member_id == member_id,
            ChatMessageReaction.emoji == emoji,
        )
    )
    rxn = result.scalar_one_or_none()
    if rxn is None:
        return  # idempotent
    await db.delete(rxn)
    await db.commit()


async def report_message(
    db: AsyncSession,
    *,
    message_id: uuid.UUID,
    reporter_id: uuid.UUID,
    reason: ReportReason,
    note: Optional[str],
) -> ChatMessageReport:
    msg = await db.get(ChatMessage, message_id)
    if msg is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Message not found"
        )

    # Reporter must currently be a member of the channel — prevents drive-by
    # reports from people who shouldn't have seen the message.
    await get_active_membership(db, msg.channel_id, reporter_id)

    # Collapse repeat reports from the same member on the same message into one
    # open report — re-reporting the same thing shouldn't multiply the queue.
    existing = await db.execute(
        select(ChatMessageReport)
        .where(
            ChatMessageReport.message_id == message_id,
            ChatMessageReport.reporter_id == reporter_id,
            ChatMessageReport.status == ReportStatus.OPEN,
        )
        .order_by(asc(ChatMessageReport.created_at))
        .limit(1)
    )
    open_existing = existing.scalar_one_or_none()
    if open_existing is not None:
        return open_existing

    report = ChatMessageReport(
        message_id=message_id,
        reporter_id=reporter_id,
        reason=reason,
        note=note,
        status=ReportStatus.OPEN,
    )
    db.add(report)

    await log_action(
        db,
        action=ChatAuditAction.REPORT_FILED,
        actor_id=reporter_id,
        channel_id=msg.channel_id,
        message_id=msg.id,
        subject_member_id=msg.sender_id,
        payload={"reason": reason.value},
    )
    await db.commit()
    await db.refresh(report)
    return report


async def get_message_with_reactions(
    db: AsyncSession,
    *,
    message_id: uuid.UUID,
    viewer_id: uuid.UUID,
) -> MessageOut:
    """Fetch a single message + its reactions in the response shape.

    Used by the edit / delete / react endpoints so they can return the updated
    row without a second roundtrip from the client."""
    msg = await db.get(ChatMessage, message_id)
    if msg is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Message not found"
        )
    rxn_rows = (
        (
            await db.execute(
                select(ChatMessageReaction).where(
                    ChatMessageReaction.message_id == message_id
                )
            )
        )
        .scalars()
        .all()
    )
    return _to_message_out(msg, viewer_id, list(rxn_rows))
