"""SessionBooking — intent-to-attend for a session.

Lives in sessions_service alongside the Session table and SessionBundleCart
because the session owns capacity gating and "who's booked this session"
is naturally a session-side query.

Represents the booking *lifecycle* only (PENDING → CONFIRMED → CANCELLED /
EXPIRED). The post-session attendance fact (PRESENT / ABSENT / LATE /
EXCUSED) lives on ``AttendanceRecord`` in attendance_service; the link is
``AttendanceRecord.booking_id`` (plain UUID, cross-service ref). Walk-in
flow doesn't create a SessionBooking — AttendanceRecord is created
directly with ``booking_id=NULL``. Pre-book flow creates SessionBooking
first; at sign-in time AttendanceRecord is created with ``booking_id`` set.

See docs/design/A1_SESSION_DISCRIMINATOR_REFACTOR.md §C for the full
design rationale, including the "single source of truth for attendance"
split between this table (in sessions_service) and AttendanceRecord
(in attendance_service).
"""

import uuid
from datetime import datetime
from typing import Optional

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from services.sessions_service.models.enums import (
    BookingChannel,
    SessionBookingStatus,
    enum_values,
)
from sqlalchemy import DateTime
from sqlalchemy import Enum as SAEnum
from sqlalchemy import CheckConstraint, Integer, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column


class SessionBooking(Base):
    """A member's pre-commitment to attend a session.

    Cross-service columns (``member_id``, ``member_auth_id``,
    ``payment_intent_id``, ``wallet_transaction_id``,
    ``corporate_program_id``) are plain UUIDs / strings without
    ForeignKey constraints, per the no-cross-service-FK architecture
    rule in docs/reference/SERVICE_COMMUNICATION.md.

    ``session_id`` is intra-service (sessions table is in this service),
    so it COULD carry a real FK — left as plain UUID for parity with the
    other cross-service refs and to keep the migration trivially reversible.
    """

    __tablename__ = "session_bookings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # Refs — plain UUIDs, no FKs (cross-service rule; session_id stays plain
    # for symmetry even though it points to a local table).
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    member_auth_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)

    # Booking lifecycle
    status: Mapped[SessionBookingStatus] = mapped_column(
        SAEnum(
            SessionBookingStatus,
            name="session_booking_status_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        nullable=False,
        default=SessionBookingStatus.PENDING,
        server_default="pending",
    )
    channel: Mapped[BookingChannel] = mapped_column(
        SAEnum(
            BookingChannel,
            name="booking_channel_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        nullable=False,
        default=BookingChannel.MEMBER_SELF,
        server_default="member_self",
    )

    # Number of swimmers this booking covers — the booking member plus any
    # guests (bring-a-friend / block booking). 1 = solo (the historical
    # default, applied to every pre-existing row). Guests themselves are rows
    # in ``booking_guests``; this is the authoritative head count for capacity
    # and fee math (fee = session.pool_fee × party_size). See
    # docs/design/GUEST_AND_GROUP_BOOKING_DESIGN.md §5a.
    party_size: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )

    # Pricing snapshot in kobo, captured at booking time so price changes
    # on the underlying Session don't retroactively alter what was owed.
    fee_amount_kobo: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    # Payment linkage (cross-service; plain UUIDs).
    payment_intent_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    wallet_transaction_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    # Corporate-wellness sponsor link (forward-looking). Plain UUID.
    corporate_program_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )

    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    booked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # TTL for PENDING bookings — 15 min default at creation time. The
    # 5-min sweep flips expired PENDING bookings to EXPIRED so the seat
    # gets released back to other members.
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    __table_args__ = (
        # At most one active booking per (session, member). The nightly
        # NO_SHOW sweep can leave the row in CONFIRMED with an
        # AttendanceRecord(status=ABSENT) referencing it — that's fine; the
        # uniqueness here prevents *double-booking*, not historical rows.
        UniqueConstraint(
            "session_id", "member_id", name="uq_session_bookings_session_member"
        ),
        # party_size is the head count (member + guests); never below 1.
        CheckConstraint("party_size >= 1", name="ck_session_bookings_party_size"),
    )

    def __repr__(self) -> str:
        return (
            f"<SessionBooking {self.id} session={self.session_id} "
            f"member={self.member_id} status={self.status.value}>"
        )
