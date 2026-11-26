"""Store post-purchase order cases (cancellations, swaps, returns, defects) — ADR-024."""

import random
import string
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from services.store_service.models.enums import (
    OrderCaseResolution,
    OrderCaseStatus,
    OrderCaseType,
    ReturnInspectionCondition,
    enum_values,
)


class StoreOrderCase(Base):
    """Auditable post-purchase request/case linked to a Store Order."""

    __tablename__ = "store_order_cases"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    case_number: Mapped[str] = mapped_column(
        String(28), unique=True, index=True, nullable=False
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("store_orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    member_auth_id: Mapped[Optional[str]] = mapped_column(
        String(255), index=True, nullable=True
    )

    case_type: Mapped[OrderCaseType] = mapped_column(
        SAEnum(
            OrderCaseType,
            values_callable=enum_values,
            name="store_order_case_type_enum",
        ),
        nullable=False,
        index=True,
    )
    status: Mapped[OrderCaseStatus] = mapped_column(
        SAEnum(
            OrderCaseStatus,
            values_callable=enum_values,
            name="store_order_case_status_enum",
        ),
        default=OrderCaseStatus.SUBMITTED,
        server_default="submitted",
        nullable=False,
        index=True,
    )

    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    customer_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    evidence_media_ids: Mapped[list] = mapped_column(
        JSONB, default=list, server_default="[]", nullable=False
    )

    preferred_resolution: Mapped[Optional[OrderCaseResolution]] = mapped_column(
        SAEnum(
            OrderCaseResolution,
            values_callable=enum_values,
            name="store_order_case_resolution_enum",
        ),
        nullable=True,
    )
    approved_resolution: Mapped[Optional[OrderCaseResolution]] = mapped_column(
        SAEnum(
            OrderCaseResolution,
            values_callable=enum_values,
            name="store_order_case_resolution_enum",
        ),
        nullable=True,
    )

    # Target variant for same-price swap or post-handover exchange
    requested_variant_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("store_product_variants.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Approved refund tender breakdown (NGN / whole Bubbles)
    approved_refund_cash_ngn: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=Decimal("0"), server_default="0", nullable=False
    )
    approved_refund_bubbles: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    approved_refund_store_credit_ngn: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=Decimal("0"), server_default="0", nullable=False
    )
    delivery_fee_refunded: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )

    # Link to shared payments_service.refunds record
    refund_reference: Mapped[Optional[str]] = mapped_column(
        String(64), index=True, nullable=True
    )
    refund_status: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    # Customer bank details (optional, for manual bank-transfer refund preference)
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

    # Physical return inspection
    return_condition: Mapped[ReturnInspectionCondition] = mapped_column(
        SAEnum(
            ReturnInspectionCondition,
            values_callable=enum_values,
            name="store_return_inspection_condition_enum",
        ),
        default=ReturnInspectionCondition.PENDING,
        server_default="pending",
        nullable=False,
    )
    return_received_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    restocked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Governance & policy override audit
    is_policy_override: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    override_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    admin_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    reviewed_by_auth_id: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolved_by_auth_id: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )
    resolved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "approved_refund_cash_ngn >= 0", name="ck_order_case_cash_nonneg"
        ),
        CheckConstraint(
            "approved_refund_bubbles >= 0", name="ck_order_case_bubbles_nonneg"
        ),
        CheckConstraint(
            "approved_refund_store_credit_ngn >= 0", name="ck_order_case_credit_nonneg"
        ),
        Index("ix_store_order_cases_order_status", "order_id", "status"),
    )

    # Relationships
    order = relationship("Order", back_populates="cases")
    items = relationship(
        "StoreOrderCaseItem", back_populates="case", cascade="all, delete-orphan"
    )
    requested_variant = relationship("ProductVariant")

    @staticmethod
    def generate_case_number() -> str:
        date_part = utc_now().strftime("%Y%m%d")
        random_part = "".join(
            random.choices(string.ascii_uppercase + string.digits, k=5)
        )
        return f"CASE-{date_part}-{random_part}"

    def __repr__(self) -> str:
        return (
            f"<StoreOrderCase {self.case_number} type={self.case_type} "
            f"status={self.status}>"
        )


class StoreOrderCaseItem(Base):
    """Specific order items and quantities covered by a StoreOrderCase."""

    __tablename__ = "store_order_case_items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("store_order_cases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    order_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("store_order_items.id", ondelete="CASCADE"),
        nullable=False,
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    replacement_variant_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("store_product_variants.id", ondelete="SET NULL"),
        nullable=True,
    )

    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_order_case_item_positive_qty"),
    )

    case = relationship("StoreOrderCase", back_populates="items")
    order_item = relationship("OrderItem")
    replacement_variant = relationship("ProductVariant")
