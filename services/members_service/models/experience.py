"""Event packages and named participant tickets owned by members-service."""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from libs.common.datetime_utils import utc_now
from libs.db.base import Base


class CommunityExperienceEvent(Base):
    __tablename__ = "community_experience_events"
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    offering_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("community_experience_offerings.id", ondelete="CASCADE"),
        index=True,
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, unique=True
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    club_impact: Mapped[str] = mapped_column(
        String(16), nullable=False, default="separate"
    )
    replaced_session_ids: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list
    )
    # Presentation snapshot only; live Event status/date is rechecked for sales.
    event_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    __table_args__ = (
        CheckConstraint(
            "club_impact IN ('separate','parallel','replaces')",
            name="ck_experience_club_impact",
        ),
    )


class CommunityExperienceOrder(Base):
    __tablename__ = "community_experience_orders"
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    offering_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("community_experience_offerings.id", ondelete="RESTRICT"),
        index=True,
    )
    member_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("members.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    member_auth_id: Mapped[str | None] = mapped_column(String, nullable=True)
    payer_email: Mapped[str] = mapped_column(String, nullable=False)
    access_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(
        String(128), nullable=False, unique=True
    )
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="pending_payment"
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    payment_reference: Mapped[str] = mapped_column(
        String(128), nullable=False, unique=True
    )
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    amount_kobo: Mapped[int] = mapped_column(Integer, nullable=False)
    checkout_url: Mapped[str | None] = mapped_column(String, nullable=True)
    membership_fee_kobo: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    membership_months: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    participants = relationship(
        "CommunityExperienceParticipant", lazy="selectin", cascade="all, delete-orphan"
    )
    __table_args__ = (
        CheckConstraint(
            "amount_kobo >= 0 AND membership_fee_kobo >= 0",
            name="ck_experience_order_amount",
        ),
    )


class CommunityExperienceParticipant(Base):
    __tablename__ = "community_experience_participants"
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("community_experience_orders.id", ondelete="CASCADE"),
        index=True,
    )
    member_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    ticket_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    full_name: Mapped[str] = mapped_column(String(160), nullable=False)
    email: Mapped[str] = mapped_column(String(254), nullable=False)
    phone: Mapped[str] = mapped_column(String(40), nullable=False)
    emergency_contact: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # Existing/bundled purchases may still need an explicit trip waiver.
    waiver_accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    price_kobo: Mapped[int] = mapped_column(Integer, nullable=False)
    __table_args__ = (
        CheckConstraint("price_kobo >= 0", name="ck_experience_participant_price"),
    )


class CommunityExperienceAttendance(Base):
    __tablename__ = "community_experience_attendance"
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    participant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("community_experience_participants.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    checked_in_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    checked_in_by: Mapped[str] = mapped_column(String, nullable=False)
    __table_args__ = (
        UniqueConstraint(
            "participant_id", "event_id", name="uq_experience_event_attendance"
        ),
    )
