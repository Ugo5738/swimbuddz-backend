"""Commercial rates and canonical people attached to a Session.

These models are additive compatibility layers. Existing Session fee columns,
SessionBooking, BookingGuest and GuestPass remain valid while callers migrate
toward explicit audience rates and one participant identity per human.
"""

import uuid
from datetime import datetime
from typing import Optional

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from sqlalchemy import CheckConstraint, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column


class SessionRate(Base):
    """One explicit commercial rate for one session audience."""

    __tablename__ = "session_rates"
    __table_args__ = (
        UniqueConstraint("session_id", "audience", name="uq_session_rate_audience"),
        CheckConstraint(
            "audience IN ('club','academy','community','guest')",
            name="ck_session_rates_audience",
        ),
        CheckConstraint(
            "price_mode IN ('included','fixed','calculated')",
            name="ck_session_rates_price_mode",
        ),
        CheckConstraint("amount_kobo >= 0", name="ck_session_rates_amount_nonnegative"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    audience: Mapped[str] = mapped_column(String(24), nullable=False)
    price_mode: Mapped[str] = mapped_column(String(24), nullable=False, default="fixed", server_default="fixed")
    amount_kobo: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    label: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100, server_default="100")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)


class SessionParticipant(Base):
    """Canonical representation of a human connected to one Session.

    Phase 1 creates these rows for true admin-entered walk-ins. Existing member
    bookings and guest-pass identities remain authoritative until later
    backfills move every attendee onto this abstraction.
    """

    __tablename__ = "session_participants"
    __table_args__ = (
        CheckConstraint(
            "participant_kind IN ('member','guest')",
            name="ck_session_participants_kind",
        ),
        CheckConstraint(
            "source IN ('member_booking','attached_guest','guest_pass','walk_in')",
            name="ck_session_participants_source",
        ),
        CheckConstraint(
            "payment_status IN ('unreconciled','not_due','pending','paid')",
            name="ck_session_participants_payment_status",
        ),
        CheckConstraint(
            "waiver_status IN ('accepted','missing','not_required')",
            name="ck_session_participants_waiver_status",
        ),
        CheckConstraint(
            "fee_amount_kobo >= 0",
            name="ck_session_participants_fee_nonnegative",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    participant_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)

    member_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    booking_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    booking_guest_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    guest_pass_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)

    full_name_snapshot: Mapped[str] = mapped_column(String(160), nullable=False)
    email_snapshot: Mapped[Optional[str]] = mapped_column(String(320), nullable=True, index=True)
    phone_snapshot: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)

    rate_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    rate_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    fee_amount_kobo: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    payment_status: Mapped[str] = mapped_column(String(24), nullable=False, default="unreconciled", server_default="unreconciled")
    waiver_status: Mapped[str] = mapped_column(String(24), nullable=False, default="missing", server_default="missing")
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)
