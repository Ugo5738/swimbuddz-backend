"""Procurement and subledger models. Values in kobo; never infer missing costs."""

import uuid
from datetime import date, datetime
from typing import Optional

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column


class StoreProcurementGrant(Base):
    __tablename__ = "store_procurement_grants"
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    auth_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    role: Mapped[str] = mapped_column(
        String(32), nullable=False, default="procurement_officer"
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    granted_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    __table_args__ = (
        CheckConstraint(
            "role IN ('procurement_officer','inventory_viewer')",
            name="ck_store_procurement_grant_role",
        ),
    )


class StorePurchaseOrder(Base):
    __tablename__ = "store_purchase_orders"
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    supplier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("store_suppliers.id"), nullable=False
    )
    order_number: Mapped[str] = mapped_column(String(70), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="draft", nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    requested_by: Mapped[str] = mapped_column(String(255), nullable=False)
    approved_by: Mapped[Optional[str]] = mapped_column(String(255))
    submitted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft','submitted','approved','partially_received','received','rejected')",
            name="ck_store_purchase_order_status",
        ),
    )


class StorePurchaseOrderLine(Base):
    __tablename__ = "store_purchase_order_lines"
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("store_purchase_orders.id"),
        nullable=False,
        index=True,
    )
    variant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("store_product_variants.id"), nullable=False
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    received: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unit_landed_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    __table_args__ = (
        UniqueConstraint(
            "order_id", "variant_id", name="uq_store_purchase_order_variant"
        ),
        CheckConstraint(
            "quantity > 0 AND received >= 0 AND received <= quantity",
            name="ck_store_purchase_line_quantity",
        ),
        CheckConstraint("unit_landed_minor >= 0", name="ck_store_purchase_unit_cost"),
    )


class StoreGoodsReceipt(Base):
    __tablename__ = "store_goods_receipts"
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("store_purchase_orders.id"), nullable=False
    )
    reference: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    evidence_reference: Mapped[str] = mapped_column(String(255), nullable=False)
    evidence_media_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True))
    ledger_entry_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True))
    ledger_sync_status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="not_posted"
    )
    actor_auth_id: Mapped[str] = mapped_column(String(255), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class StoreGoodsReceiptLine(Base):
    __tablename__ = "store_goods_receipt_lines"
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    receipt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("store_goods_receipts.id"), nullable=False
    )
    purchase_line_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("store_purchase_order_lines.id"), nullable=False
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_landed_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    __table_args__ = (
        UniqueConstraint(
            "receipt_id", "purchase_line_id", name="uq_store_goods_receipt_line"
        ),
        CheckConstraint("quantity > 0", name="ck_store_goods_receipt_quantity"),
    )


class StoreInventoryValuation(Base):
    __tablename__ = "store_inventory_valuations"
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    variant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("store_product_variants.id"),
        unique=True,
        nullable=False,
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_cost_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    __table_args__ = (
        CheckConstraint(
            "quantity >= 0 AND total_cost_minor >= 0",
            name="ck_store_inventory_valuation",
        ),
    )


class StoreCostEvent(Base):
    __tablename__ = "store_cost_events"
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    variant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("store_product_variants.id"), nullable=False
    )
    event_kind: Mapped[str] = mapped_column(String(30), nullable=False)
    quantity_delta: Mapped[int] = mapped_column(Integer, nullable=False)
    cost_delta_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reference: Mapped[str] = mapped_column(String(180), unique=True, nullable=False)
    evidence: Mapped[str] = mapped_column(String(255), nullable=False)
    actor_auth_id: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class StoreSupplierInvoice(Base):
    __tablename__ = "store_supplier_invoices"
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    supplier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("store_suppliers.id"), nullable=False
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("store_purchase_orders.id"), nullable=False
    )
    reference: Mapped[str] = mapped_column(String(120), nullable=False)
    total_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    verified_paid_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )
    due_on: Mapped[Optional[date]] = mapped_column(Date)
    evidence_media_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True))
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    __table_args__ = (
        UniqueConstraint("supplier_id", "reference", name="uq_store_supplier_invoice"),
        CheckConstraint(
            "total_minor > 0 AND verified_paid_minor >= 0 AND verified_paid_minor <= total_minor",
            name="ck_store_supplier_invoice_amount",
        ),
    )


class StoreSupplierInvoiceSettlement(Base):
    """Append-only Finance Owner certified settlements against a supplier invoice."""

    __tablename__ = "store_supplier_invoice_settlements"
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("store_supplier_invoices.id"),
        nullable=False,
        index=True,
    )
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    bank_reference: Mapped[str] = mapped_column(String(180), nullable=False)
    treasury_request_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True))
    ledger_journal_entry_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True)
    )
    evidence_media_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True))
    settled_by: Mapped[str] = mapped_column(String(255), nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    settled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    __table_args__ = (
        UniqueConstraint(
            "invoice_id",
            "bank_reference",
            name="uq_store_supplier_invoice_settlement_ref",
        ),
        CheckConstraint(
            "amount_minor > 0", name="ck_store_supplier_invoice_settlement_positive"
        ),
    )


class StoreTradeReceivable(Base):
    __tablename__ = "store_trade_receivables"
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("store_orders.id"), unique=True, nullable=False
    )
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    verified_paid_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="requested")
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    due_on: Mapped[date] = mapped_column(Date, nullable=False)
    requested_by: Mapped[str] = mapped_column(String(255), nullable=False)
    approved_by: Mapped[Optional[str]] = mapped_column(String(255))
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    settled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    ledger_entry_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True))
    ledger_sync_status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="not_posted"
    )
    __table_args__ = (
        CheckConstraint(
            "amount_minor > 0 AND verified_paid_minor >= 0 AND verified_paid_minor <= amount_minor",
            name="ck_store_trade_receivable_amount",
        ),
        CheckConstraint(
            "status IN ('requested','approved','rejected','settled')",
            name="ck_store_trade_receivable_status",
        ),
    )


class StoreTradeReceivablePayment(Base):
    """Append-only verified partial or full customer credit repayments."""

    __tablename__ = "store_trade_receivable_payments"
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    receivable_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("store_trade_receivables.id"),
        nullable=False,
        index=True,
    )
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    bank_reference: Mapped[str] = mapped_column(
        String(180), unique=True, nullable=False
    )
    bank_account_ref: Mapped[str] = mapped_column(
        String(80), nullable=False, default="bank_operating_ngn"
    )
    evidence_media_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True))
    ledger_entry_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True))
    ledger_sync_status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="not_posted"
    )
    verified_by: Mapped[str] = mapped_column(String(255), nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    paid_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    __table_args__ = (
        CheckConstraint(
            "amount_minor > 0", name="ck_store_trade_receivable_payment_positive"
        ),
    )


class StoreOpeningReconciliation(Base):
    __tablename__ = "store_opening_reconciliations"
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    variant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("store_product_variants.id"), nullable=False
    )
    source_reference: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False
    )
    evidence_media_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True))
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    total_cost_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    proposed_by: Mapped[str] = mapped_column(String(255), nullable=False)
    approved_by: Mapped[Optional[str]] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    ledger_entry_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True))
    ledger_sync_status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="not_posted"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    __table_args__ = (
        CheckConstraint(
            "quantity >= 0 AND total_cost_minor >= 0", name="ck_store_opening_value"
        ),
        CheckConstraint(
            "status IN ('pending','approved','rejected')",
            name="ck_store_opening_status",
        ),
    )
