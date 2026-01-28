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
    PENDING = "pending"  # Initial state, awaiting payment
    PENDING_REVIEW = "pending_review"  # Manual payment submitted, awaiting admin review
    PAID = "paid"
    WAIVED = "waived"
    FAILED = "failed"


class PaymentMethod(str, enum.Enum):
    PAYSTACK = "paystack"  # Online payment via Paystack
    MANUAL_TRANSFER = "manual_transfer"  # Bank transfer with proof upload


class PaymentPurpose(str, enum.Enum):
    # Tier activations
    COMMUNITY = "community"  # Community membership (₦20,000/year)
    CLUB = "club"  # Club add-on (requires Community active)
    CLUB_BUNDLE = "club_bundle"  # Community + Club together (new member bundle)
    ACADEMY_COHORT = "academy_cohort"  # Specific cohort enrollment

    # One-off fees
    SESSION_FEE = "session_fee"  # Pool fees, ride share, event tickets

    # E-commerce
    STORE_ORDER = "store_order"  # Store product purchase


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

    # Payment method (paystack for online, manual_transfer for bank transfer)
    payment_method: Mapped[str | None] = mapped_column(
        String(32), default="paystack", nullable=True
    )
    # Media ID for proof of payment - links to media_service.media_items (cross-service)
    # Used for manual bank transfer proof uploads
    proof_of_payment_media_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    # Admin review note (for rejected payments)
    admin_review_note: Mapped[str | None] = mapped_column(Text, nullable=True)

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


class PayoutStatus(str, enum.Enum):
    """Status of a coach payout."""

    PENDING = "pending"  # Calculated, awaiting admin action
    APPROVED = "approved"  # Admin approved, ready for transfer
    PROCESSING = "processing"  # Transfer initiated (Paystack or manual)
    PAID = "paid"  # Successfully transferred
    FAILED = "failed"  # Transfer failed


class PayoutMethod(str, enum.Enum):
    """Method used for coach payout."""

    PAYSTACK_TRANSFER = "paystack_transfer"  # Automated via Paystack
    BANK_TRANSFER = "bank_transfer"  # Manual bank transfer
    OTHER = "other"  # Cash, other platforms, etc.


class CoachPayout(Base):
    """Coach payout records.

    Tracks earnings and payout status for coaches. Supports both
    automated Paystack transfers and manual payment methods.
    """

    __tablename__ = "coach_payouts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # Coach reference (cross-service - references members.id)
    coach_member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), index=True, nullable=False
    )

    # Period this payout covers
    period_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    period_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    period_label: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # e.g., "January 2026"

    # Earnings breakdown (in smallest currency unit - kobo for NGN)
    academy_earnings: Mapped[int] = mapped_column(
        default=0, nullable=False
    )  # From cohorts
    session_earnings: Mapped[int] = mapped_column(
        default=0, nullable=False
    )  # From 1-on-1
    other_earnings: Mapped[int] = mapped_column(default=0, nullable=False)  # Bonuses
    total_amount: Mapped[int] = mapped_column(nullable=False)  # Sum
    currency: Mapped[str] = mapped_column(String(8), default="NGN", nullable=False)

    # Status
    status: Mapped[PayoutStatus] = mapped_column(
        SAEnum(PayoutStatus, name="payout_status_enum"),
        default=PayoutStatus.PENDING,
        nullable=False,
    )

    # Payment method (admin chooses when initiating)
    payout_method: Mapped[PayoutMethod | None] = mapped_column(
        SAEnum(PayoutMethod, name="payout_method_enum"),
        nullable=True,
    )

    # Admin actions
    approved_by: Mapped[str | None] = mapped_column(String, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Payment tracking
    paid_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    payment_reference: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )  # Bank ref or Paystack transfer_code

    # Paystack transfer specific
    paystack_transfer_code: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )
    paystack_transfer_status: Mapped[str | None] = mapped_column(
        String(20), nullable=True
    )  # pending, success, failed

    # Notes
    admin_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    @staticmethod
    def generate_reference() -> str:
        suffix = "".join(random.choices(string.digits, k=6))
        return f"PAYOUT-{suffix}"

    def __repr__(self):
        return f"<CoachPayout {self.id} {self.period_label} {self.status.value}>"
