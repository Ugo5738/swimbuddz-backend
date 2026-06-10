"""BookingGuest — a non-member swimmer attached to a SessionBooking.

A member can bring guests (bring-a-friend) or reserve a block of slots; each
swimmer beyond the booking member is one row here. Guests are NOT members:
no account, no enrolment, no make-up rights (see
docs/design/GUEST_AND_GROUP_BOOKING_DESIGN.md §5b + D4). Captured identity
(name + phone, guardian for minors) doubles as a safeguarding record and a
conversion lead — ``converted_member_id`` closes the funnel when a guest later
signs up as a member.

``booking_id`` references SessionBooking in this same service. Like the other
refs in booking.py it is stored as a plain UUID with no ForeignKey, for parity
with the cross-service-no-FK convention and trivially reversible migrations.
"""

import uuid
from datetime import date as _Date
from datetime import datetime
from typing import Optional

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from sqlalchemy import CheckConstraint, Date, DateTime, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column


class BookingGuest(Base):
    """A non-member swimmer covered by a SessionBooking (§5b)."""

    __tablename__ = "booking_guests"

    __table_args__ = (
        # Lightweight value check in lieu of a Postgres ENUM type — enum TYPE
        # names are global across all services' shared schema, so a generic
        # name risks collision. Pydantic ``GuestIntent`` validates at the API.
        CheckConstraint(
            "intent IN ('social', 'trial')",
            name="ck_booking_guests_intent",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # Parent booking — intra-service ref, plain UUID (see module docstring).
    booking_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )

    # Identity. Nullable so a block booking can reserve slots now and name them
    # before check-in; the check-in path hard-requires a name (and guardian +
    # waiver for minors) before an AttendanceRecord is created (§9 / D6).
    full_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(Text, nullable=True, index=True)

    # 'social' = open-meet friend (self-serve) | 'trial' = prospective student
    # sampling a class (coach-approved, D10). See CheckConstraint above.
    intent: Mapped[str] = mapped_column(
        Text, nullable=False, default="social", server_default="social"
    )

    # Minor gate (§9 / D6): a DOB implying age < 18 at the session date makes
    # guardian_name + guardian_phone + waiver_accepted_at mandatory at check-in.
    date_of_birth: Mapped[Optional[_Date]] = mapped_column(Date, nullable=True)
    guardian_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    guardian_phone: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    waiver_accepted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Lead funnel (Phase 3) — set when this guest later becomes a member.
    converted_member_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    def __repr__(self) -> str:
        return (
            f"<BookingGuest {self.id} booking={self.booking_id} "
            f"intent={self.intent}>"
        )
