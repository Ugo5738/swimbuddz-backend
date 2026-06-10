import uuid
from datetime import datetime
from typing import Optional

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from services.attendance_service.models.enums import (
    AttendanceRole,
    AttendanceStatus,
    enum_values,
)
from sqlalchemy import DateTime
from sqlalchemy import Enum as SAEnum
from sqlalchemy import CheckConstraint, String, UniqueConstraint
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


class AttendanceRecord(Base):
    __tablename__ = "attendance_records"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    # Nullable so a non-member GUEST attendance row can be keyed on
    # booking_guest_id instead (a CHECK enforces exactly one). Every existing
    # row carries member_id, so the relaxation is backwards-compatible.
    member_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )

    # Attendance status/details
    status: Mapped[AttendanceStatus] = mapped_column(
        SAEnum(
            AttendanceStatus,
            name="attendance_status_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        default=AttendanceStatus.PRESENT,
    )
    role: Mapped[AttendanceRole] = mapped_column(
        SAEnum(
            AttendanceRole,
            name="attendance_role_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        default=AttendanceRole.SWIMMER,
    )

    notes: Mapped[str] = mapped_column(String, nullable=True)

    # Wallet payment reference (nullable — only set when paid with Bubbles)
    wallet_transaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    # Pre-booking linkage (A1 Phase 3.3). NULL = walk-in (member signed in
    # without booking ahead). Set = this attendance row was produced by an
    # existing SessionBooking in sessions_service — used to compute
    # no-show stats: AttendanceRecord.status='absent' AND booking_id IS NOT NULL.
    # Plain UUID, no FK — SessionBooking lives in sessions_service per the
    # cross-service-no-FK architecture rule. Cleanup of orphan booking_ids
    # on SessionBooking deletion is handled by HTTP-emitted events
    # (see services/sessions_service/services/attendance_sync.py).
    booking_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
    )

    # Cross-service ref → sessions_service.booking_guests.id (plain UUID, no
    # FK). Set when this row records a non-member GUEST's attendance; member_id
    # is then NULL. See docs/design/GUEST_AND_GROUP_BOOKING_DESIGN.md §5c.
    booking_guest_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    # Relationships (optional, but useful)
    # session = relationship("Session")
    # member = relationship("Member")

    __table_args__ = (
        UniqueConstraint(
            "session_id", "member_id", name="uq_session_member_attendance"
        ),
        # A guest can't be double-recorded for a session. Member rows keep the
        # constraint above; Postgres treats NULLs as distinct, so member rows
        # (booking_guest_id NULL) and guest rows (member_id NULL) never clash.
        UniqueConstraint(
            "session_id",
            "booking_guest_id",
            name="uq_session_booking_guest_attendance",
        ),
        # Exactly one subject per row — a member XOR a guest, never both/neither.
        CheckConstraint(
            "(member_id IS NOT NULL) <> (booking_guest_id IS NOT NULL)",
            name="ck_attendance_member_xor_guest",
        ),
    )

    def __repr__(self):
        return f"<AttendanceRecord Session={self.session_id} Member={self.member_id}>"
