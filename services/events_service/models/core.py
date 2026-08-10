"""Events Service models for SwimBuddz."""

import uuid
from datetime import date, datetime, time
from typing import Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Time,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from libs.common.datetime_utils import utc_now
from libs.db.base import Base


class MemberRef(Base):
    """Reference to shared members table without cross-service imports."""

    __tablename__ = "members"
    __table_args__ = {"extend_existing": True, "info": {"skip_autogenerate": True}}

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    auth_id: Mapped[str] = mapped_column(
        String, unique=True, index=True, nullable=False
    )


class Event(Base):
    """Community events like social gatherings, beach days, etc."""

    __tablename__ = "events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(String, nullable=True)
    event_type: Mapped[str] = mapped_column(
        String, nullable=False
    )  # social/volunteer/beach_day/watch_party/cleanup/training
    # Audience describes the programme lane shown on the calendar. It is
    # intentionally separate from tier_access: an Academy assessment can be
    # public and open to prospects while still belonging to Academy.
    audience: Mapped[str] = mapped_column(
        String, nullable=False, default="community", server_default="community"
    )
    visibility: Mapped[str] = mapped_column(
        String, nullable=False, default="public", server_default="public"
    )  # public/members_only/invite_only
    status: Mapped[str] = mapped_column(
        String, nullable=False, default="published", server_default="published"
    )  # draft/published/cancelled
    location_type: Mapped[str] = mapped_column(
        String, nullable=False, default="physical", server_default="physical"
    )  # physical/online/hybrid
    timezone: Mapped[str] = mapped_column(
        String, nullable=False, default="Africa/Lagos", server_default="Africa/Lagos"
    )
    location_area: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    is_location_private: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    location: Mapped[str] = mapped_column(
        String, nullable=True
    )  # "Federal Palace Hotel, VI", "Rowe Park, Yaba"
    start_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    max_capacity: Mapped[int] = mapped_column(Integer, nullable=True)
    # Optional entry fee in kobo (null = free). API accepts/returns naira (float).
    cost_kobo: Mapped[int] = mapped_column(Integer, nullable=True)
    pricing_mode: Mapped[str] = mapped_column(
        String(24), nullable=False, default="fixed", server_default="fixed"
    )  # free/included/fixed/cost_plus
    pricing_expected_attendees: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )
    cost_lines: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    estimated_total_cost: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    estimated_cost_per_attendee: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    margin_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="fixed_per_attendee",
        server_default="fixed_per_attendee",
    )
    # fixed_per_attendee: kobo; percentage: basis points (20% = 2000).
    margin_value: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    margin_amount_per_attendee: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    email_reminder_hours: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    tier_access: Mapped[str] = mapped_column(
        String, default="community"
    )  # public/community/club/academy/invite_only
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    template_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("event_templates.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    external_key: Mapped[Optional[str]] = mapped_column(
        String, nullable=True, unique=True, index=True
    )

    # --- Member-created pool meets (event_type="open_swim") ---
    # Selected pool for a paid pool meet. Plain cross-service ref to pools_service
    # (no FK by architecture). NULL = no pool / informal venue / free meet.
    # Members may only select active-partner pools that bill per-swimmer.
    pool_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    # Per-swimmer pool fee snapshotted from the pool at creation (kobo). NULL = free.
    pool_fee_kobo: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Organizer's optional add-on charged per attendee (kobo). Collected into the
    # company account; the organizer's share is disbursed manually off-platform.
    organizer_surcharge_kobo: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    def __repr__(self):
        return f"<Event {self.title}>"


class EventTemplate(Base):
    """Reusable rule for generating draft community calendar events."""

    __tablename__ = "event_templates"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    audience: Mapped[str] = mapped_column(
        String, nullable=False, default="community", server_default="community"
    )
    visibility: Mapped[str] = mapped_column(
        String, nullable=False, default="public", server_default="public"
    )
    location_type: Mapped[str] = mapped_column(
        String, nullable=False, default="physical", server_default="physical"
    )
    timezone: Mapped[str] = mapped_column(
        String, nullable=False, default="Africa/Lagos", server_default="Africa/Lagos"
    )
    location_area: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    is_location_private: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    location: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    local_start_time: Mapped[time] = mapped_column(Time, nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    max_capacity: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    tier_access: Mapped[str] = mapped_column(
        String, nullable=False, default="community", server_default="community"
    )
    pool_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    cost_kobo: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    pricing_mode: Mapped[str] = mapped_column(
        String(24), nullable=False, default="fixed", server_default="fixed"
    )
    pricing_expected_attendees: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )
    cost_lines: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    estimated_total_cost: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    estimated_cost_per_attendee: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    margin_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="fixed_per_attendee",
        server_default="fixed_per_attendee",
    )
    margin_value: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    margin_amount_per_attendee: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    email_reminder_hours: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)

    frequency: Mapped[str] = mapped_column(String, nullable=False)
    interval: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    day_of_week: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    week_of_month: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    day_of_month: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    month_of_year: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    starts_on: Mapped[date] = mapped_column(Date, nullable=False)
    ends_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class EventRSVP(Base):
    """RSVP status for members attending events."""

    __tablename__ = "event_rsvps"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    member_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)  # going/maybe/not_going
    wallet_transaction_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    def __repr__(self):
        return f"<EventRSVP event={self.event_id} member={self.member_id} status={self.status}>"


class EventInvite(Base):
    """Explicit access grant for an invite-only event."""

    __tablename__ = "event_invites"
    __table_args__ = (
        UniqueConstraint(
            "event_id",
            "member_id",
            name="uq_event_invites_event_member",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("events.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )


class EventReminderLog(Base):
    """Idempotency record for one event reminder sent to one member."""

    __tablename__ = "event_reminder_logs"
    __table_args__ = (
        UniqueConstraint(
            "event_id",
            "member_id",
            "reminder_hours",
            name="uq_event_reminder_event_member_offset",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("events.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    reminder_hours: Mapped[int] = mapped_column(Integer, nullable=False)
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
