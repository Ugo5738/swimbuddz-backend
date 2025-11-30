import enum
import uuid
from datetime import datetime

from libs.db.base import Base
from sqlalchemy import DateTime
from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column


class AttendanceStatus(str, enum.Enum):
    PRESENT = "PRESENT"
    ABSENT = "ABSENT"
    LATE = "LATE"
    EXCUSED = "EXCUSED"
    CANCELLED = "CANCELLED"


class AttendanceRole(str, enum.Enum):
    SWIMMER = "SWIMMER"
    COACH = "COACH"
    VOLUNTEER = "VOLUNTEER"
    GUEST = "GUEST"


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

    # Attendance status/details (simplified; ride-share and payment move to dedicated services)
    status: Mapped[AttendanceStatus] = mapped_column(
        SAEnum(AttendanceStatus, name="attendance_status_enum"),
        default=AttendanceStatus.PRESENT,
    )
    role: Mapped[AttendanceRole] = mapped_column(
        SAEnum(AttendanceRole, name="attendance_role_enum"),
        default=AttendanceRole.SWIMMER,
    )

    notes: Mapped[str] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
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
