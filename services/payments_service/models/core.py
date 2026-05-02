import random
import string
import uuid
from datetime import datetime

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from services.payments_service.models.enums import (
    DiscountType,
    MakeupReason,
    MakeupStatus,
    PaymentPurpose,
    PaymentStatus,
    PayoutMethod,
    PayoutStatus,
    RecurringPayoutStatus,
    enum_values,
)
from sqlalchemy import DateTime
from sqlalchemy import Enum as SAEnum
from sqlalchemy import Float, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column


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
        SAEnum(
            PaymentPurpose,
            name="payment_purpose_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        nullable=False,
    )

    amount: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="NGN", nullable=False)

    status: Mapped[PaymentStatus] = mapped_column(
        SAEnum(
            PaymentStatus,
            name="payment_status_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
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
        SAEnum(
            DiscountType,
            name="discount_type_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
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
        SAEnum(
            PayoutStatus,
            name="payout_status_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        default=PayoutStatus.PENDING,
        nullable=False,
    )

    # Payment method (admin chooses when initiating)
    payout_method: Mapped[PayoutMethod | None] = mapped_column(
        SAEnum(
            PayoutMethod,
            name="payout_method_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
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


class RecurringPayoutConfig(Base):
    """Per-cohort, per-coach recurring payout configuration.

    Stores only the policy (band percentage); naira amounts are derived at
    payout time from cohort price + delivered sessions + make-ups completed.
    The cron job iterates these configs daily and creates PENDING CoachPayout
    rows when next_run_date is reached, advancing block_index until total_blocks.
    """

    __tablename__ = "recurring_payout_configs"
    __table_args__ = (
        UniqueConstraint(
            "coach_member_id", "cohort_id", name="uq_recurring_payout_coach_cohort"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # Cross-service references (no FK constraints — academy_service owns cohort,
    # members_service owns coach profile)
    coach_member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), index=True, nullable=False
    )
    cohort_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), index=True, nullable=False
    )

    # The single rate stored. Per-block / per-session naira amounts are
    # derived at runtime from (cohort.price × band_percentage / total_blocks).
    # Decimal(5,2) supports values like 38.50, 42.00 etc.
    band_percentage: Mapped[float] = mapped_column(
        Numeric(5, 2), nullable=False
    )

    # Cohort plan snapshot (taken at config creation time so the payout
    # schedule is stable even if cohort dates shift later).
    total_blocks: Mapped[int] = mapped_column(Integer, nullable=False)
    block_length_days: Mapped[int] = mapped_column(
        Integer, default=28, nullable=False
    )  # 4 weeks
    cohort_start_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    cohort_end_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # Snapshot of cohort price (per student, kobo). Held here so historical
    # payouts compute correctly even if cohort.price_amount changes later.
    cohort_price_amount: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="NGN", nullable=False)

    # Schedule tracking
    block_index: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )  # 0..total_blocks-1; equals "blocks already paid out"
    next_run_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True, nullable=False
    )

    # Lifecycle
    status: Mapped[RecurringPayoutStatus] = mapped_column(
        SAEnum(
            RecurringPayoutStatus,
            name="recurring_payout_status_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        default=RecurringPayoutStatus.ACTIVE,
        nullable=False,
        index=True,
    )

    # Audit
    created_by_member_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    def __repr__(self):
        return (
            f"<RecurringPayoutConfig coach={self.coach_member_id} "
            f"cohort={self.cohort_id} band={self.band_percentage}% "
            f"block {self.block_index}/{self.total_blocks}>"
        )


class CohortMakeupObligation(Base):
    """A make-up session owed to a student in a cohort.

    Auto-created when:
      - A student enrolls late (LATE_JOIN: one obligation per session that
        ran before their enrolled_at).
      - A coach marks a session as EXCUSED for a student (EXCUSED_ABSENCE).
      - A scheduled session is cancelled (SESSION_CANCELLED).

    Coach schedules a make-up session (the scheduled_session_id links to the
    sessions table). When attendance is marked PRESENT/LATE on that session,
    the obligation flips to COMPLETED and the next block's payout includes
    pay for the make-up. If the cohort end_date passes without a make-up,
    a sweeper flips the obligation to EXPIRED (no pay credited).
    """

    __tablename__ = "cohort_makeup_obligations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # Cross-service references
    cohort_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), index=True, nullable=False
    )
    student_member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), index=True, nullable=False
    )
    coach_member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), index=True, nullable=False
    )
    # The session that was originally missed (if applicable). Nullable for
    # late-join obligations where there are N missed sessions before the
    # student joined — we store one row per missed session and reference it.
    original_session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    # The make-up session the coach has scheduled (links to sessions table).
    scheduled_session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )

    reason: Mapped[MakeupReason] = mapped_column(
        SAEnum(
            MakeupReason,
            name="makeup_reason_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        nullable=False,
    )
    status: Mapped[MakeupStatus] = mapped_column(
        SAEnum(
            MakeupStatus,
            name="makeup_status_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        default=MakeupStatus.PENDING,
        nullable=False,
        index=True,
    )

    # When the make-up was actually completed (PRESENT/LATE attendance marked).
    # Used to attribute pay to the right block.
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # When pay was credited (links to a CoachPayout row that included this).
    pay_credited_in_payout_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    def __repr__(self):
        return (
            f"<CohortMakeupObligation cohort={self.cohort_id} "
            f"student={self.student_member_id} reason={self.reason.value} "
            f"status={self.status.value}>"
        )
