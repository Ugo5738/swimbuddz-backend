import enum
import random
import string
import uuid
from datetime import datetime

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from sqlalchemy import DateTime
from sqlalchemy import Enum as SAEnum
from sqlalchemy import Float, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column


class PaymentStatus(str, enum.Enum):
    PENDING = "pending"
    PAID = "paid"
    WAIVED = "waived"
    FAILED = "failed"


class PaymentPurpose(str, enum.Enum):
    # Tier activations
    COMMUNITY = "community"  # Community membership (₦20,000/year)
    CLUB = "club"  # Club add-on (requires Community active)
    CLUB_BUNDLE = "club_bundle"  # Community + Club together (new member bundle)
    ACADEMY_COHORT = "academy_cohort"  # Specific cohort enrollment

    # One-off fees
    SESSION_FEE = "session_fee"  # Pool fees, ride share, event tickets


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    reference: Mapped[str] = mapped_column(
        String, unique=True, index=True, nullable=False
    )

    member_auth_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    payer_email: Mapped[str | None] = mapped_column(String, index=True, nullable=True)

    purpose: Mapped[PaymentPurpose] = mapped_column(
        SAEnum(PaymentPurpose, name="payment_purpose_enum"),
        nullable=False,
    )

    amount: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="NGN", nullable=False)

    status: Mapped[PaymentStatus] = mapped_column(
        SAEnum(PaymentStatus, name="payment_status_enum"),
        default=PaymentStatus.PENDING,
        nullable=False,
    )

    provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    provider_reference: Mapped[str | None] = mapped_column(
        String(128), index=True, nullable=True
    )
    paid_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    entitlement_applied_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    entitlement_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # "metadata" is reserved by SQLAlchemy's Declarative API, so we map the DB column
    # named "metadata" onto a safe attribute name.
    payment_metadata: Mapped[dict | None] = mapped_column(
        "metadata", JSONB, nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    @staticmethod
    def generate_reference() -> str:
        suffix = "".join(random.choices(string.digits, k=5))
        return f"PAY-{suffix}"

    def __repr__(self):
        return f"<Payment {self.reference}>"


class DiscountType(str, enum.Enum):
    PERCENTAGE = "percentage"  # e.g., 10% off
    FIXED = "fixed"  # e.g., ₦5,000 off


class Discount(Base):
    """Discount codes that can be applied to payments."""

    __tablename__ = "discounts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    code: Mapped[str] = mapped_column(
        String(50), unique=True, index=True, nullable=False
    )
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)

    discount_type: Mapped[DiscountType] = mapped_column(
        SAEnum(DiscountType, name="discount_type_enum"),
        nullable=False,
    )
    value: Mapped[float] = mapped_column(Float, nullable=False)  # % or fixed amount

    # Which payment purposes this discount applies to (JSON array)
    applies_to: Mapped[list | None] = mapped_column(
        JSONB,
        nullable=True,  # ["COMMUNITY", "CLUB", "ACADEMY_COHORT"]
    )

    # Validity period
    valid_from: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    valid_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Usage limits
    max_uses: Mapped[int | None] = mapped_column(nullable=True)  # None = unlimited
    current_uses: Mapped[int] = mapped_column(default=0, nullable=False)

    # Per-user limit
    max_uses_per_user: Mapped[int | None] = mapped_column(nullable=True)

    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    def __repr__(self):
        return f"<Discount {self.code}>"
