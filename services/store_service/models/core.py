"""Store service models for e-commerce functionality.

Domain entities:
- Catalog: Categories, Products, Variants, Images, Collections
- Inventory: Stock tracking, Reservations, Movements
- Cart: Shopping cart and line items
- Orders: Order lifecycle, items, fulfillment
- Store Credits: Refund credits (all-sales-final policy)
- Pickup Locations: Dynamic from database
- Audit Logs: Track sensitive changes
"""

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
    InventoryMovementType,
    OrderStatus,
    ProductStatus,
    SourcingType,
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
# REFERENCE MODELS (cross-service references without imports)
# ============================================================================


class MemberRef(Base):
    """Reference to shared members table without cross-service imports."""

    __tablename__ = "members"
    __table_args__ = {"extend_existing": True, "info": {"skip_autogenerate": True}}

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )


# ============================================================================
# CATALOG MODELS
# ============================================================================


class Category(Base):
    """Product categories (e.g., 'Goggles', 'Swimwear', 'Training Aids')."""

    __tablename__ = "store_categories"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    image_media_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )  # FK to media_service.media_items

    # Subcategory support
    parent_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("store_categories.id", ondelete="SET NULL"),
        nullable=True,
    )

    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    # Relationships
    parent = relationship("Category", remote_side=[id], back_populates="children")
    children = relationship("Category", back_populates="parent")
    products = relationship("Product", back_populates="category")

    def __repr__(self):
        return f"<Category {self.name}>"


class Product(Base):
    """Products available in the store (e.g., 'SwimBuddz Pro Goggles')."""

    __tablename__ = "store_products"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    category_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("store_categories.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Basic info
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    short_description: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # Pricing (base, before discounts) - stored in kobo for precision
    base_price_ngn: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    compare_at_price_ngn: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(12, 2), nullable=True
    )  # "was" price for sales display

    # Status
    status: Mapped[ProductStatus] = mapped_column(
        SAEnum(
            ProductStatus,
            values_callable=enum_values,
            name="store_product_status_enum",
        ),
        default=ProductStatus.DRAFT,
        server_default="draft",
    )
    is_featured: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )

    # SEO
    meta_title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    meta_description: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # Variant configuration
    has_variants: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    variant_options: Mapped[Optional[dict]] = mapped_column(
        JSONB, nullable=True
    )  # e.g., {"Size": ["S","M","L"], "Color": ["Black","Blue"]}

    # Sourcing
    sourcing_type: Mapped[SourcingType] = mapped_column(
        SAEnum(
            SourcingType,
            values_callable=enum_values,
            name="store_sourcing_type_enum",
        ),
        default=SourcingType.STOCKED,
        server_default="stocked",
    )
    preorder_lead_days: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )  # e.g., 21 for "ships in 3 weeks"

    # Size chart requirement (for swimwear)
    requires_size_chart_ack: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    size_chart_media_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )  # FK to media_service.media_items

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    # Relationships
    category = relationship("Category", back_populates="products")
    variants = relationship(
        "ProductVariant", back_populates="product", cascade="all, delete-orphan"
    )
    images = relationship(
        "ProductImage", back_populates="product", cascade="all, delete-orphan"
    )
    collection_products = relationship(
        "CollectionProduct", back_populates="product", cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<Product {self.name}>"


class ProductVariant(Base):
    """Product variants (e.g., 'Pro Goggles - Blue - Adult')."""

    __tablename__ = "store_product_variants"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("store_products.id", ondelete="CASCADE"),
        nullable=False,
    )

    sku: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    name: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )  # Auto-generated from options

    # Options (e.g., {"Size": "M", "Color": "Blue"})
    options: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # Pricing override (null = use product base price)
    price_override_ngn: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(12, 2), nullable=True
    )

    # Physical attributes (for future shipping calc)
    weight_grams: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Status
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    # Relationships
    product = relationship("Product", back_populates="variants")
    inventory_item = relationship(
        "InventoryItem",
        back_populates="variant",
        uselist=False,
        cascade="all, delete-orphan",
    )
    images = relationship("ProductImage", back_populates="variant")

    def __repr__(self):
        return f"<ProductVariant {self.sku}>"


class ProductImage(Base):
    """Product images."""

    __tablename__ = "store_product_images"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("store_products.id", ondelete="CASCADE"),
        nullable=False,
    )
    variant_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("store_product_variants.id", ondelete="SET NULL"),
        nullable=True,
    )

    url: Mapped[str] = mapped_column(String(512), nullable=False)
    alt_text: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    is_primary: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )

    # Relationships
    product = relationship("Product", back_populates="images")
    variant = relationship("ProductVariant", back_populates="images")

    def __repr__(self):
        return f"<ProductImage {self.id}>"


class Collection(Base):
    """Curated product collections (e.g., 'New Arrivals', 'Coach Favorites')."""

    __tablename__ = "store_collections"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    image_media_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )  # FK to media_service.media_items

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

    # Relationships
    collection_products = relationship(
        "CollectionProduct", back_populates="collection", cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<Collection {self.name}>"


class CollectionProduct(Base):
    """Junction table for collection-product relationships."""

    __tablename__ = "store_collection_products"

    collection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("store_collections.id", ondelete="CASCADE"),
        primary_key=True,
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("store_products.id", ondelete="CASCADE"),
        primary_key=True,
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    # Relationships
    collection = relationship("Collection", back_populates="collection_products")
    product = relationship("Product", back_populates="collection_products")


# ============================================================================
# INVENTORY MODELS
# ============================================================================


class InventoryItem(Base):
    """Inventory tracking per variant."""

    __tablename__ = "store_inventory_items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    variant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("store_product_variants.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )

    # Stock levels
    quantity_on_hand: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    quantity_reserved: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )  # Held in active carts

    # Thresholds
    low_stock_threshold: Mapped[int] = mapped_column(
        Integer, default=5, server_default="5"
    )

    # Tracking
    last_restock_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_sold_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    __table_args__ = (
        CheckConstraint("quantity_on_hand >= 0", name="positive_stock"),
        CheckConstraint(
            "quantity_reserved >= 0 AND quantity_reserved <= quantity_on_hand",
            name="valid_reserved",
        ),
    )

    # Relationships
    variant = relationship("ProductVariant", back_populates="inventory_item")
    movements = relationship(
        "InventoryMovement",
        back_populates="inventory_item",
        cascade="all, delete-orphan",
    )

    @property
    def quantity_available(self) -> int:
        """Available quantity (on hand minus reserved)."""
        return self.quantity_on_hand - self.quantity_reserved

    def __repr__(self):
        return f"<InventoryItem variant={self.variant_id} qty={self.quantity_on_hand}>"


class InventoryMovement(Base):
    """Audit trail for inventory changes."""

    __tablename__ = "store_inventory_movements"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    inventory_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("store_inventory_items.id", ondelete="CASCADE"),
        nullable=False,
    )

    movement_type: Mapped[InventoryMovementType] = mapped_column(
        SAEnum(
            InventoryMovementType,
            values_callable=enum_values,
            name="store_inventory_movement_type_enum",
        ),
        nullable=False,
    )
    quantity: Mapped[int] = mapped_column(
        Integer, nullable=False
    )  # Positive = add, negative = subtract

    reference_type: Mapped[Optional[str]] = mapped_column(
        String(30), nullable=True
    )  # order, cart, manual
    reference_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    performed_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )

    # Relationships
    inventory_item = relationship("InventoryItem", back_populates="movements")

    def __repr__(self):
        return f"<InventoryMovement {self.movement_type} qty={self.quantity}>"


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
