"""Store commerce models: cart, orders, pickup locations, credits, audit logs."""

import random
import string
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from services.store_service.models.enums import (
    AuditEntityType,
    CartStatus,
    FulfillmentType,
    OrderStatus,
    StoreCreditSourceType,
    enum_values,
)
from sqlalchemy import Boolean, CheckConstraint, DateTime
from sqlalchemy import Enum as SAEnum
from sqlalchemy import (
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

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
    total_ngn: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    # Discounts applied
    discount_code: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    discount_breakdown: Mapped[Optional[dict]] = mapped_column(
        JSONB, nullable=True
    )  # {"code": "SWIM10", "member_tier": "club", "total_saved": 2500}

    # Status
    status: Mapped[OrderStatus] = mapped_column(
        SAEnum(
            OrderStatus,
            values_callable=enum_values,
            name="store_order_status_enum",
        ),
        default=OrderStatus.PENDING_PAYMENT,
        server_default="pending_payment",
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
    fulfilled_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(
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
    pickup_location = relationship("PickupLocation")

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

    # For pre-order items
    is_preorder: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    estimated_ship_date: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

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


class StoreAuditLog(Base):
    """Audit log for sensitive store operations."""

    __tablename__ = "store_audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    entity_type: Mapped[AuditEntityType] = mapped_column(
        SAEnum(
            AuditEntityType,
            values_callable=enum_values,
            name="store_audit_entity_type_enum",
        ),
        nullable=False,
    )
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    action: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # e.g., "price_changed", "stock_adjusted"

    old_value: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    new_value: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    performed_by: Mapped[str] = mapped_column(String(255), nullable=False)
    performed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )

    ip_address: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_store_audit_logs_entity", "entity_type", "entity_id"),
        Index("ix_store_audit_logs_performed_at", "performed_at"),
    )

    def __repr__(self):
        return f"<StoreAuditLog {self.entity_type}:{self.entity_id} {self.action}>"
