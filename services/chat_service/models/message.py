"""Chat message and reaction models.

Per-message read-receipts table intentionally deferred (see design §4.1);
unread state is derived from `chat_channel_members.last_read_message_id`.
"""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from services.chat_service.models.enums import SafeguardingReviewState, enum_values


class ChatMessage(Base):
    """A single chat message. Soft-delete only: setting `deleted_at` preserves
    the row for audit — hard-delete only via safeguarding admin action after
    retention expires (design §9)."""

    __tablename__ = "chat_messages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    channel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_channels.id", ondelete="CASCADE"),
        nullable=False,
    )
    sender_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    # Markdown subset (see design §8.1). 4,000 char ceiling — above that it's a
    # document, not a chat message. We still use Text on the DB side so the
    # cap can move without a schema change.
    body: Mapped[str] = mapped_column(Text, nullable=False)
    # Array of attachment descriptors: [{type, storage_key, mime, size, thumbnail}].
    attachments: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    reply_to_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_messages.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    edited_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deleted_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    safeguarding_review_state: Mapped[SafeguardingReviewState] = mapped_column(
        SAEnum(
            SafeguardingReviewState,
            name="chat_safeguarding_review_state_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        nullable=False,
        default=SafeguardingReviewState.NONE,
    )
    meta_data: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict, server_default="{}"
    )

    # Relationships
    channel: Mapped["ChatChannel"] = relationship(  # noqa: F821
        back_populates="messages"
    )
    reactions: Mapped[list["ChatMessageReaction"]] = relationship(
        back_populates="message", cascade="all, delete-orphan", lazy="selectin"
    )
    # Self-referential: replies pointing at this message. Not eager-loaded.
    replies: Mapped[list["ChatMessage"]] = relationship(
        "ChatMessage",
        remote_side="ChatMessage.reply_to_id",
        lazy="noload",
    )

    __table_args__ = (
        # Most-common query: "give me recent messages in this channel"
        Index(
            "ix_chat_messages_channel_recent",
            "channel_id",
            "created_at",
        ),
        # Moderator surface: "what has this member sent recently?"
        Index(
            "ix_chat_messages_sender_recent",
            "sender_id",
            "created_at",
        ),
        # Safeguarding work queue: only flagged rows matter
        Index(
            "ix_chat_messages_flagged",
            "safeguarding_review_state",
            postgresql_where="safeguarding_review_state = 'flagged'",
        ),
    )

    def __repr__(self) -> str:
        preview = (self.body or "")[:30].replace("\n", " ")
        return (
            f"<ChatMessage {self.id} channel={self.channel_id} "
            f"sender={self.sender_id} body={preview!r}>"
        )


class ChatMessageReaction(Base):
    """An emoji reaction on a message. Composite PK (message_id, member_id, emoji)
    enforces "one reaction per emoji per member per message"."""

    __tablename__ = "chat_message_reactions"

    message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_messages.id", ondelete="CASCADE"),
        primary_key=True,
    )
    member_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    emoji: Mapped[str] = mapped_column(String(32), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    # Relationships
    message: Mapped["ChatMessage"] = relationship(back_populates="reactions")

    def __repr__(self) -> str:
        return (
            f"<ChatMessageReaction message={self.message_id} member={self.member_id} "
            f"emoji={self.emoji}>"
        )
