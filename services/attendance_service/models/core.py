import uuid
from datetime import datetime

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from services.attendance_service.models.enums import (
    AttendanceRole,
    AttendanceStatus,
    enum_values,
)
from sqlalchemy import DateTime
from sqlalchemy import Enum as SAEnum
from sqlalchemy import String, UniqueConstraint
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
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
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
    )

    def __repr__(self):
        return f"<AttendanceRecord Session={self.session_id} Member={self.member_id}>"
