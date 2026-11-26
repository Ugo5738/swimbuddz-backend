"""First-class shared Refund model (ADR-023).

Owned by payments_service and reusable across Store, Academy, Club, and Sessions
while each owning domain service keeps its own cancellation/return policy.
"""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, validates

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from services.payments_service.models.enums import (
    RefundMethod,
    RefundStatus,
    enum_values,
)


class Refund(Base):
    """Auditable refund record linking a payment and source entity to disbursement."""

    __tablename__ = "refunds"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    refund_reference: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(
        String(160), unique=True, index=True, nullable=False
    )

    # Original cash payment (nullable only when an order/purchase had zero cash tender)
    payment_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("payments.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    payment_reference: Mapped[Optional[str]] = mapped_column(
        String(128), index=True, nullable=True
    )

    member_auth_id: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    payer_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Source domain reference (no cross-service FK)
    source_service: Mapped[str] = mapped_column(String(40), index=True, nullable=False)
    source_entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_entity_id: Mapped[str] = mapped_column(
        String(128), index=True, nullable=False
    )
    purpose: Mapped[str] = mapped_column(String(64), nullable=False)
    currency: Mapped[str] = mapped_column(
        String(8), default="NGN", server_default="NGN", nullable=False
    )

    # Exact minor-unit / whole-Bubble tender breakdown
    requested_total_kobo: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0", nullable=False
    )
    cash_refund_kobo: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0", nullable=False
    )
    bubbles_refund: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    store_credit_refund_kobo: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0", nullable=False
    )

    refund_method: Mapped[RefundMethod] = mapped_column(
        SAEnum(
            RefundMethod,
            name="refund_method_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        nullable=False,
    )
    status: Mapped[RefundStatus] = mapped_column(
        SAEnum(
            RefundStatus,
            name="refund_status_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        default=RefundStatus.REQUESTED,
        server_default="requested",
        nullable=False,
        index=True,
    )

    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    admin_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    override_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Provider / bank disbursement tracking
    provider: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    provider_refund_id: Mapped[Optional[str]] = mapped_column(
        String(128), index=True, nullable=True
    )
    provider_status: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    provider_payload: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    external_bank_reference: Mapped[Optional[str]] = mapped_column(
        String(128), index=True, nullable=True
    )

    # Verified recipient bank account for manual bank-transfer refunds
    recipient_bank_code: Mapped[Optional[str]] = mapped_column(
        String(32), nullable=True
    )
    recipient_bank_name: Mapped[Optional[str]] = mapped_column(
        String(128), nullable=True
    )
    recipient_account_number: Mapped[Optional[str]] = mapped_column(
        String(32), nullable=True
    )
    recipient_account_name: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )
    recipient_account_verified: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )

    wallet_refund_transaction_id: Mapped[Optional[str]] = mapped_column(
        String(128), nullable=True
    )
    store_credit_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    # Two-step ledger flags (ADR-023)
    ledger_accrual_posted: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    ledger_disbursement_posted: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )

    # Actor & lifecycle timestamps
    requested_by_auth_id: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )
    approved_by_auth_id: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )
    approved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    initiated_by_auth_id: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )
    initiated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_by_auth_id: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    failed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    failure_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    refund_metadata: Mapped[Optional[dict]] = mapped_column(
        "metadata", JSONB, nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "requested_total_kobo >= 0", name="ck_refunds_requested_total_nonneg"
        ),
        CheckConstraint("cash_refund_kobo >= 0", name="ck_refunds_cash_nonneg"),
        CheckConstraint("bubbles_refund >= 0", name="ck_refunds_bubbles_nonneg"),
        CheckConstraint(
            "store_credit_refund_kobo >= 0", name="ck_refunds_store_credit_nonneg"
        ),
        Index(
            "ix_refunds_source_lookup",
            "source_service",
            "source_entity_id",
        ),
    )

    @staticmethod
    def generate_reference() -> str:
        return f"REF-{uuid.uuid4().hex[:12].upper()}"

    @validates("status")
    def _coerce_status(self, key, value):
        if isinstance(value, str):
            if value in RefundStatus.__members__:
                return RefundStatus[value]
            try:
                return RefundStatus(value)
            except ValueError:
                return value
        return value

    @validates("refund_method")
    def _coerce_method(self, key, value):
        if isinstance(value, str):
            if value in RefundMethod.__members__:
                return RefundMethod[value]
            try:
                return RefundMethod(value)
            except ValueError:
                return value
        return value

    def __repr__(self) -> str:
        return (
            f"<Refund {self.refund_reference} status={self.status} "
            f"cash_kobo={self.cash_refund_kobo}>"
        )
