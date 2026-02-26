"""Store catalog models: categories, products, variants, images, collections."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from services.store_service.models.enums import ProductStatus, SourcingType, enum_values
from sqlalchemy import Boolean, DateTime
from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Integer, Numeric, String, Text
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
