"""Events Service models for SwimBuddz."""

import uuid
from datetime import datetime
from typing import Optional

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from sqlalchemy import DateTime, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column


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
    tier_access: Mapped[str] = mapped_column(
        String, default="community"
    )  # minimum tier required: community/club/academy
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

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
