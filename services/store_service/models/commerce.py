"""Store commerce models: cart, orders, pickup locations, credits, audit logs."""

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
    UniqueConstraint,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from libs.common.audit import AuditLogMixin
from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from services.store_service.models.enums import (
    CartStatus,
    FulfillmentType,
    OrderFulfillmentStatus,
    OrderPaymentStatus,
    OrderStatus,
    StoreCreditSourceType,
    enum_values,
)

# ============================================================================
# CART MODELS
# ============================================================================


class Cart(Base):
    """Shopping carts."""

    __tablename__ = "store_carts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # Owner (member_auth_id for logged in, session_id for guests)
    member_auth_id: Mapped[Optional[str]] = mapped_column(
        String(255), index=True, nullable=True
    )
    session_id: Mapped[Optional[str]] = mapped_column(
        String(255), index=True, nullable=True
    )

    # Applied discounts
    discount_code: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    member_discount_percent: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(5, 2), nullable=True
    )  # Calculated from tier

    # Status
    status: Mapped[CartStatus] = mapped_column(
        SAEnum(
            CartStatus,
            values_callable=enum_values,
            name="store_cart_status_enum",
        ),
        default=CartStatus.ACTIVE,
        server_default="active",
    )

    # Expiry for inventory reservation release
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    __table_args__ = (
        CheckConstraint(
            "member_auth_id IS NOT NULL OR session_id IS NOT NULL",
            name="cart_one_owner",
        ),
        Index("ix_store_carts_member_auth_id_status", "member_auth_id", "status"),
    )

    # Relationships
    items = relationship(
        "CartItem", back_populates="cart", cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<Cart {self.id} status={self.status}>"


class CartItem(Base):
    """Cart line items."""

    __tablename__ = "store_cart_items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    cart_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("store_carts.id", ondelete="CASCADE"),
        nullable=False,
    )
    variant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("store_product_variants.id"),
        nullable=False,
    )

    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    # Snapshot price at add time (for comparison if price changes)
    unit_price_ngn: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    __table_args__ = (
        UniqueConstraint("cart_id", "variant_id", name="unique_cart_variant"),
        CheckConstraint("quantity > 0", name="positive_quantity"),
    )

    # Relationships
    cart = relationship("Cart", back_populates="items")
    variant = relationship("ProductVariant")

    def __repr__(self):
        return f"<CartItem variant={self.variant_id} qty={self.quantity}>"


# ============================================================================
# ORDER MODELS
# ============================================================================


class Order(Base):
    """Orders."""

    __tablename__ = "store_orders"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    order_number: Mapped[str] = mapped_column(
        String(20), unique=True, nullable=False, index=True
    )

    # Customer
    member_auth_id: Mapped[Optional[str]] = mapped_column(
        String(255), index=True, nullable=True
    )
    customer_email: Mapped[str] = mapped_column(String(255), nullable=False)
    customer_name: Mapped[str] = mapped_column(String(255), nullable=False)
    customer_phone: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    # Pricing (in NGN)
    subtotal_ngn: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    discount_amount_ngn: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=0, server_default="0"
    )
    store_credit_applied_ngn: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=0, server_default="0"
    )
    delivery_fee_ngn: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=0, server_default="0"
    )
    # True net order value before tenders (subtotal - discount + delivery).
    # Nullable for legacy rows; use effective_order_total_ngn for reads.
    order_total_ngn: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    # Legacy cash-payable remainder (after store_credit_applied_ngn and bubbles_applied).
    total_ngn: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    # Discounts applied
    discount_code: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    discount_breakdown: Mapped[Optional[dict]] = mapped_column(
        JSONB, nullable=True
    )  # {"code": "SWIM10", "member_tier": "club", "total_saved": 2500}

    # Legacy combined status (kept synced with payment_status + fulfillment_status)
    status: Mapped[OrderStatus] = mapped_column(
        SAEnum(
            OrderStatus,
            values_callable=enum_values,
            name="store_order_status_enum",
        ),
        default=OrderStatus.PENDING_PAYMENT,
        server_default="pending_payment",
    )
    # Separated status dimensions (ADR-024)
    payment_status: Mapped[Optional[OrderPaymentStatus]] = mapped_column(
        SAEnum(
            OrderPaymentStatus,
            values_callable=enum_values,
            name="store_order_payment_status_enum",
        ),
        nullable=True,
        index=True,
    )
    fulfillment_status: Mapped[Optional[OrderFulfillmentStatus]] = mapped_column(
        SAEnum(
            OrderFulfillmentStatus,
            values_callable=enum_values,
            name="store_order_fulfillment_status_enum",
        ),
        nullable=True,
        index=True,
    )

    # Payment reference (links to payments_service)
    payment_reference: Mapped[Optional[str]] = mapped_column(
        String(100), index=True, nullable=True
    )

    # Bubbles wallet payment (null if paid by other method)
    bubbles_applied: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    wallet_transaction_id: Mapped[Optional[str]] = mapped_column(
        String(100), index=True, nullable=True
    )
    wallet_hold_id: Mapped[Optional[str]] = mapped_column(
        String(100), index=True, nullable=True
    )

    # Fulfillment
    fulfillment_type: Mapped[FulfillmentType] = mapped_column(
        SAEnum(
            FulfillmentType,
            values_callable=enum_values,
            name="store_fulfillment_type_enum",
        ),
        default=FulfillmentType.PICKUP,
        server_default="pickup",
    )

    # Pickup details
    pickup_location_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("store_pickup_locations.id", ondelete="SET NULL"),
        nullable=True,
    )
    pickup_session_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )  # Optional: link to specific session for pickup

    # Delivery details (for home delivery)
    delivery_address: Mapped[Optional[dict]] = mapped_column(
        JSONB, nullable=True
    )  # {"street": "...", "city": "...", "state": "...", "phone": "..."}
    delivery_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Notes
    customer_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    admin_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Timestamps
    paid_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    shipped_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    delivered_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    fulfilled_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    refunded_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    # Relationships
    items = relationship(
        "OrderItem", back_populates="order", cascade="all, delete-orphan"
    )
    pickup_location = relationship(
        "services.store_service.models.commerce.PickupLocation"
    )
    cases = relationship(
        "StoreOrderCase", back_populates="order", cascade="all, delete-orphan"
    )

    @property
    def effective_order_total_ngn(self) -> Decimal:
        """True net order value before store credit, Bubbles, and cash splits."""
        if self.order_total_ngn is not None:
            return Decimal(self.order_total_ngn)
        return max(
            Decimal("0"),
            Decimal(self.subtotal_ngn or 0)
            - Decimal(self.discount_amount_ngn or 0)
            + Decimal(self.delivery_fee_ngn or 0),
        )

    @property
    def cash_payable_ngn(self) -> Decimal:
        """Cash portion settled via Paystack or manual bank transfer."""
        return Decimal(self.total_ngn or 0)

    @property
    def effective_payment_status(self) -> OrderPaymentStatus:
        """Resolved payment status, deriving safely for legacy rows."""
        if self.payment_status is not None:
            return self.payment_status
        mapping = {
            OrderStatus.PENDING_PAYMENT: OrderPaymentStatus.UNPAID,
            OrderStatus.PAYMENT_FAILED: OrderPaymentStatus.FAILED,
            OrderStatus.PAID: OrderPaymentStatus.PAID,
            OrderStatus.PROCESSING: OrderPaymentStatus.PAID,
            OrderStatus.READY_FOR_PICKUP: OrderPaymentStatus.PAID,
            OrderStatus.PICKED_UP: OrderPaymentStatus.PAID,
            OrderStatus.SHIPPED: OrderPaymentStatus.PAID,
            OrderStatus.DELIVERED: OrderPaymentStatus.PAID,
            OrderStatus.REFUNDED: OrderPaymentStatus.REFUNDED,
            OrderStatus.CANCELLED: (
                OrderPaymentStatus.PAID if self.paid_at else OrderPaymentStatus.UNPAID
            ),
        }
        return mapping.get(self.status, OrderPaymentStatus.UNPAID)

    @property
    def effective_fulfillment_status(self) -> OrderFulfillmentStatus:
        """Resolved fulfillment status, deriving safely for legacy rows."""
        if self.fulfillment_status is not None:
            return self.fulfillment_status
        mapping = {
            OrderStatus.PENDING_PAYMENT: OrderFulfillmentStatus.NOT_STARTED,
            OrderStatus.PAYMENT_FAILED: OrderFulfillmentStatus.NOT_STARTED,
            OrderStatus.PAID: OrderFulfillmentStatus.AWAITING_PREPARATION,
            OrderStatus.PROCESSING: OrderFulfillmentStatus.PROCESSING,
            OrderStatus.READY_FOR_PICKUP: OrderFulfillmentStatus.READY_FOR_PICKUP,
            OrderStatus.PICKED_UP: OrderFulfillmentStatus.PICKED_UP,
            OrderStatus.SHIPPED: OrderFulfillmentStatus.SHIPPED,
            OrderStatus.DELIVERED: OrderFulfillmentStatus.DELIVERED,
            OrderStatus.CANCELLED: OrderFulfillmentStatus.CANCELLED,
            OrderStatus.REFUNDED: (
                OrderFulfillmentStatus.RETURNED
                if self.fulfilled_at
                else OrderFulfillmentStatus.CANCELLED
            ),
        }
        return mapping.get(self.status, OrderFulfillmentStatus.NOT_STARTED)

    @staticmethod
    def generate_order_number() -> str:
        """Generate a unique order number like SB-20260104-A1B2C."""
        date_part = datetime.utcnow().strftime("%Y%m%d")
        random_part = "".join(
            random.choices(string.ascii_uppercase + string.digits, k=5)
        )
        return f"SB-{date_part}-{random_part}"

    def __repr__(self):
        return f"<Order {self.order_number}>"


class OrderItem(Base):
    """Order line items (snapshot at order time)."""

    __tablename__ = "store_order_items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("store_orders.id", ondelete="CASCADE"),
        nullable=False,
    )
    variant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("store_product_variants.id"),
        nullable=False,
    )

    # Snapshot at order time (products may change)
    product_name: Mapped[str] = mapped_column(String(255), nullable=False)
    variant_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    sku: Mapped[str] = mapped_column(String(100), nullable=False)

    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price_ngn: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    line_total_ngn: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    # For pre-order / sourcing & return-policy snapshots (ADR-024)
    is_preorder: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    sourcing_type: Mapped[str] = mapped_column(
        String(24), default="stocked", server_default="stocked", nullable=False
    )
    net_line_total_ngn: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    requires_size_chart_ack: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    supplier_committed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    estimated_ship_date: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Supplier snapshot (Phase 2 -- captured at order time)
    supplier_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    supplier_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )

    # Relationships
    order = relationship("Order", back_populates="items")
    variant = relationship("ProductVariant")

    def __repr__(self):
        return f"<OrderItem {self.product_name} qty={self.quantity}>"


# ============================================================================
# PICKUP LOCATION MODEL
# ============================================================================


class PickupLocation(Base):
    """Pickup locations (dynamic, from database)."""

    __tablename__ = "store_pickup_locations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)  # e.g., "Yaba Pool"
    address: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Contact for pickup coordination
    contact_phone: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    contact_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Optional link to pools_service (soft reference, no FK constraint across services)
    pool_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )

    # Availability
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    def __repr__(self):
        return f"<PickupLocation {self.name}>"


# ============================================================================
# STORE CREDIT MODELS
# ============================================================================


class StoreCredit(Base):
    """Store credits for refunds (all-sales-final policy)."""

    __tablename__ = "store_credits"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    member_auth_id: Mapped[str] = mapped_column(String(255), index=True, nullable=False)

    amount_ngn: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    balance_ngn: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    # Source
    source_type: Mapped[StoreCreditSourceType] = mapped_column(
        SAEnum(
            StoreCreditSourceType,
            values_callable=enum_values,
            name="store_credit_source_type_enum",
        ),
        nullable=False,
    )
    source_order_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("store_orders.id", ondelete="SET NULL"),
        nullable=True,
    )
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Optional expiry
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    issued_by: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )  # Admin auth_id

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )

    # Relationships
    source_order = relationship("Order")
    transactions = relationship(
        "StoreCreditTransaction",
        back_populates="store_credit",
        cascade="all, delete-orphan",
    )

    def __repr__(self):
        return f"<StoreCredit {self.id} balance={self.balance_ngn}>"


class StoreCreditTransaction(Base):
    """Store credit usage log."""

    __tablename__ = "store_credit_transactions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    store_credit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("store_credits.id", ondelete="CASCADE"),
        nullable=False,
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("store_orders.id", ondelete="CASCADE"),
        nullable=False,
    )

    amount_ngn: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    transaction_type: Mapped[str] = mapped_column(
        String(16), default="debit", server_default="debit", nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )

    # Relationships
    store_credit = relationship("StoreCredit", back_populates="transactions")
    order = relationship("Order")

    def __repr__(self):
        return f"<StoreCreditTransaction {self.amount_ngn}>"


# ============================================================================
# AUDIT LOG MODEL
# ============================================================================


class StoreAuditLog(AuditLogMixin, Base):
    """Audit log for sensitive store operations.

    Adopts the canonical shape from :class:`libs.common.audit.AuditLogMixin`
    (B4 unification). The service-local ``AuditEntityType`` enum is kept
    in code as a validation/typing aid at the write site, but the DB
    column is a free string (canonical shape) so the per-service
    vocabulary can grow without DB migrations.
    """

    __tablename__ = "store_audit_logs"

    __table_args__ = (
        Index("ix_store_audit_entity_created", "entity_id", "created_at"),
        Index("ix_store_audit_created_at", "created_at"),
    )

    def __repr__(self):
        return f"<StoreAuditLog {self.entity_type}:{self.entity_id} {self.action}>"
