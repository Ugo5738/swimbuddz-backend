import uuid
from datetime import datetime
import enum
import random
import string

from sqlalchemy import String, Float, DateTime, Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from libs.db.base import Base
from services.attendance_service.models import PaymentStatus


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    reference: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[PaymentStatus] = mapped_column(
        SAEnum(PaymentStatus, name="payment_status_enum", create_type=False), # Reuse existing enum type
        default=PaymentStatus.PENDING,
        nullable=False
    )
    
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )

    @staticmethod
    def generate_reference() -> str:
        """Generates a unique payment reference like PAY-12345."""
        # Simple implementation: PAY- + 5 random digits
        # In production, check for collision or use a sequence
        suffix = ''.join(random.choices(string.digits, k=5))
        return f"PAY-{suffix}"

    def __repr__(self):
        return f"<Payment {self.reference}>"
