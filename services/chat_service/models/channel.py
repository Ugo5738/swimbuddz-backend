"""Chat channel and channel-member models.

Channels are the top-level chat container (cohort, pod, event, trip, DM, etc.).
Membership is derived from parent entities (enrollment, RSVP, ...) wherever
possible — see design doc §4.2.
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
from services.chat_service.models.enums import (
    ChannelMemberRole,
    ChannelType,
    MembershipDerivation,
    ParentEntityType,
    RetentionPolicy,
    enum_values,
)


class ChatChannel(Base):
    """A chat channel. Type is immutable after creation — see design §3."""

    __tablename__ = "chat_channels"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    type: Mapped[ChannelType] = mapped_column(
        SAEnum(
            ChannelType,
            name="chat_channel_type_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        nullable=False,
    )
    parent_entity_type: Mapped[ParentEntityType] = mapped_column(
        SAEnum(
            ParentEntityType,
            name="chat_parent_entity_type_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        nullable=False,
        default=ParentEntityType.NONE,
    )
    parent_entity_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    archived_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    retention_policy: Mapped[RetentionPolicy] = mapped_column(
        SAEnum(
            RetentionPolicy,
            name="chat_retention_policy_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        nullable=False,
    )
    # Flags used by safeguarding enforcement — e.g. {"has_minors": true}.
    # Kept as JSONB so new flags can be added without migrations.
    safeguarding_flags: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    # Extensible metadata bag — avoid clashing with SQLAlchemy's reserved
    # `metadata` attribute by using `meta_data` (column name still "metadata").
    meta_data: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict, server_default="{}"
    )

    # Relationships (within service)
    members: Mapped[list["ChatChannelMember"]] = relationship(
        back_populates="channel", cascade="all, delete-orphan", lazy="selectin"
    )
    messages: Mapped[list["ChatMessage"]] = relationship(  # noqa: F821
        back_populates="channel", cascade="all, delete-orphan", lazy="noload"
    )

    __table_args__ = (
        # Find-by-parent is the most common query (e.g. "give me Cohort 05's channel")
        Index("ix_chat_channels_parent", "parent_entity_type", "parent_entity_id"),
        # Fast filter for live (non-archived) channels
        Index(
            "ix_chat_channels_active",
            "id",
            postgresql_where="archived_at IS NULL",
        ),
    )

    def __repr__(self) -> str:
        return f"<ChatChannel {self.id} type={self.type.value} name={self.name!r}>"


class ChatChannelMember(Base):
    """Membership of a member in a channel.

    Composite primary key (channel_id, member_id). Members who leave a channel
    keep their row with `left_at` set — never hard-deleted, for audit.
    """

    __tablename__ = "chat_channel_members"

    channel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_channels.id", ondelete="CASCADE"),
        primary_key=True,
    )
    member_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    role: Mapped[ChannelMemberRole] = mapped_column(
        SAEnum(
            ChannelMemberRole,
            name="chat_channel_member_role_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        nullable=False,
        default=ChannelMemberRole.MEMBER,
    )
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    left_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    muted_until: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Pointer to the most recent message the member has "read" in this channel.
    # Used to derive unread counts; explicit per-message reads table deferred —
    # see design doc §4.1 decision note.
    last_read_message_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    derived_from: Mapped[MembershipDerivation] = mapped_column(
        SAEnum(
            MembershipDerivation,
            name="chat_membership_derivation_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        nullable=False,
        default=MembershipDerivation.MANUAL,
    )
    # Points back to the parent record (e.g. enrollment_id, rsvp_id). Used by
    # the nightly reconciliation job to detect drift between chat membership
    # and source-of-truth state in other services.
    derivation_ref: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    # Relationships (within service)
    channel: Mapped["ChatChannel"] = relationship(back_populates="members")

    __table_args__ = (
        # "What channels am I currently in?" — very hot query
        Index(
            "ix_chat_channel_members_active_by_member",
            "member_id",
            postgresql_where="left_at IS NULL",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<ChatChannelMember channel={self.channel_id} member={self.member_id} "
            f"role={self.role.value}>"
        )
