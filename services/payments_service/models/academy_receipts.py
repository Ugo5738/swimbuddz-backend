"""One verified external bank receipt, many immutable beneficiary allocations.

These records are financial attribution, not additional cash receipts.
"""

import uuid

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    CheckConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from libs.common.datetime_utils import utc_now
from libs.db.base import Base


class AcademyBankReceipt(Base):
    __tablename__ = "academy_bank_receipts"
    __table_args__ = (
        CheckConstraint("amount_kobo > 0", name="ck_academy_receipt_positive"),
        UniqueConstraint(
            "external_reference", name="uq_academy_receipt_external_reference"
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    payment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("payments.id"), nullable=False, unique=True
    )
    external_reference: Mapped[str] = mapped_column(String(160), nullable=False)
    amount_kobo: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="NGN")
    verification_note: Mapped[str] = mapped_column(Text, nullable=False)
    verified_by_auth_id: Mapped[str] = mapped_column(String(160), nullable=False)
    verified_at = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    created_at = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class AcademyReceiptAllocation(Base):
    __tablename__ = "academy_receipt_allocations"
    __table_args__ = (
        CheckConstraint(
            "amount_kobo > 0", name="ck_academy_receipt_allocation_positive"
        ),
        UniqueConstraint(
            "receipt_id",
            "idempotency_key",
            name="uq_academy_receipt_allocation_idempotency",
        ),
        Index(
            "ix_academy_receipt_allocation_enrollment_state", "enrollment_id", "state"
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    receipt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("academy_bank_receipts.id"),
        nullable=False,
        index=True,
    )
    member_auth_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    enrollment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    amount_kobo: Mapped[int] = mapped_column(BigInteger, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="reserved")
    created_by_auth_id: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    applied_at = mapped_column(DateTime(timezone=True), nullable=True)
    voided_by_auth_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    void_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    voided_at = mapped_column(DateTime(timezone=True), nullable=True)
