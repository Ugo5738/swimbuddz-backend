import uuid
from datetime import datetime
import enum

from sqlalchemy import String, Integer, Float, DateTime, Enum as SAEnum, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from libs.db.base import Base


class PaymentStatus(str, enum.Enum):
    PENDING = "pending"
    PAID = "paid"
    WAIVED = "waived"
    FAILED = "failed"


class SessionAttendance(Base):
    __tablename__ = "session_attendance"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sessions.id"), nullable=False, index=True
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("members.id"), nullable=False, index=True
    )
    
    # Ride share info
    needs_ride: Mapped[bool] = mapped_column(default=False)
    can_offer_ride: Mapped[bool] = mapped_column(default=False)
    ride_notes: Mapped[str] = mapped_column(String, nullable=True)
    
    # Payment info
    payment_status: Mapped[PaymentStatus] = mapped_column(
        SAEnum(PaymentStatus, name="payment_status_enum"), 
        default=PaymentStatus.PENDING,
        nullable=False
    )
    total_fee: Mapped[float] = mapped_column(Float, default=0.0)
    
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Relationships (optional, but useful)
    # session = relationship("Session")
    # member = relationship("Member")

    __table_args__ = (
        UniqueConstraint("session_id", "member_id", name="uq_session_member_attendance"),
    )

    def __repr__(self):
        return f"<Attendance Session={self.session_id} Member={self.member_id}>"
