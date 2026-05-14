"""Product, variant, image, and video schemas."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from services.store_service.models import ProductStatus, ProductType, SourcingType

from .category import CategoryResponse


class ProductBase(BaseModel):
    name: str = Field(..., max_length=255)
    slug: str = Field(..., max_length=255)
    category_id: Optional[uuid.UUID] = None
    product_type: ProductType = ProductType.STANDARD
    description: Optional[str] = None
    short_description: Optional[str] = Field(None, max_length=500)
    base_price_ngn: Decimal = Field(..., ge=0)
    compare_at_price_ngn: Optional[Decimal] = Field(None, ge=0)
    status: ProductStatus = ProductStatus.DRAFT
    is_featured: bool = False
    meta_title: Optional[str] = Field(None, max_length=255)
    meta_description: Optional[str] = Field(None, max_length=500)
    has_variants: bool = False
    variant_options: Optional[dict] = None
    sourcing_type: SourcingType = SourcingType.STOCKED
    preorder_lead_days: Optional[int] = Field(None, ge=1)
    requires_size_chart_ack: bool = False
    size_chart_media_id: Optional[uuid.UUID] = None
    supplier_id: Optional[uuid.UUID] = None
    cost_price_ngn: Optional[Decimal] = Field(None, ge=0)


class ProductCreate(ProductBase):
    pass


class ProductUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=255)
    slug: Optional[str] = Field(None, max_length=255)
    category_id: Optional[uuid.UUID] = None
    product_type: Optional[ProductType] = None
    description: Optional[str] = None
    short_description: Optional[str] = Field(None, max_length=500)
    base_price_ngn: Optional[Decimal] = Field(None, ge=0)
    compare_at_price_ngn: Optional[Decimal] = Field(None, ge=0)
    status: Optional[ProductStatus] = None
    is_featured: Optional[bool] = None
    meta_title: Optional[str] = Field(None, max_length=255)
    meta_description: Optional[str] = Field(None, max_length=500)
    has_variants: Optional[bool] = None
    variant_options: Optional[dict] = None
    sourcing_type: Optional[SourcingType] = None
    preorder_lead_days: Optional[int] = Field(None, ge=1)
    requires_size_chart_ack: Optional[bool] = None
    size_chart_media_id: Optional[uuid.UUID] = None
    supplier_id: Optional[uuid.UUID] = None
    cost_price_ngn: Optional[Decimal] = Field(None, ge=0)


class ProductVariantBase(BaseModel):
    sku: str = Field(..., max_length=100)
    name: Optional[str] = Field(None, max_length=255)
    options: dict = Field(default_factory=dict)
    price_override_ngn: Optional[Decimal] = Field(None, ge=0)
    weight_grams: Optional[int] = Field(None, ge=0)
    is_active: bool = True


class ProductVariantCreate(BaseModel):
    """SKU is optional — auto-generated from product slug if omitted."""

    sku: Optional[str] = Field(None, max_length=100)
    name: Optional[str] = Field(None, max_length=255)
    options: dict = Field(default_factory=dict)
    price_override_ngn: Optional[Decimal] = Field(None, ge=0)
    weight_grams: Optional[int] = Field(None, ge=0)
    is_active: bool = True


class ProductVariantUpdate(BaseModel):
    sku: Optional[str] = Field(None, max_length=100)
    name: Optional[str] = Field(None, max_length=255)
    options: Optional[dict] = None
    price_override_ngn: Optional[Decimal] = Field(None, ge=0)
    weight_grams: Optional[int] = Field(None, ge=0)
    is_active: Optional[bool] = None


class ProductVariantResponse(ProductVariantBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class ProductVariantWithInventory(ProductVariantResponse):
    """Admin variant response - includes full inventory details."""

    quantity_available: int = 0
    quantity_on_hand: int = 0


class PublicProductVariantInfo(ProductVariantResponse):
    """Public variant response - hides internal inventory counts."""

    in_stock: bool = True
    quantity_available: int = 0


class ProductImageBase(BaseModel):
    url: str = Field(..., max_length=512)
    alt_text: Optional[str] = Field(None, max_length=255)
    sort_order: int = 0
    is_primary: bool = False
    variant_id: Optional[uuid.UUID] = None


class ProductImageCreate(ProductImageBase):
    pass


class ProductImageUpdate(BaseModel):
    url: Optional[str] = Field(None, max_length=512)
    alt_text: Optional[str] = Field(None, max_length=255)
    sort_order: Optional[int] = None
    is_primary: Optional[bool] = None
    variant_id: Optional[uuid.UUID] = None


class ProductImageResponse(ProductImageBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product_id: uuid.UUID
    created_at: datetime


class ProductVideoBase(BaseModel):
    url: str = Field(..., max_length=512)
    thumbnail_url: Optional[str] = Field(None, max_length=512)
    title: Optional[str] = Field(None, max_length=255)
    sort_order: int = 0
    media_item_id: Optional[uuid.UUID] = None


class ProductVideoCreate(ProductVideoBase):
    pass


class ProductVideoResponse(ProductVideoBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product_id: uuid.UUID
    is_processed: bool = True
    created_at: datetime


class DefaultVariantResponse(BaseModel):
    """Minimal variant info for quick-add on product cards."""

    id: uuid.UUID
    sku: str


class ProductResponse(ProductBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    size_chart_url: Optional[str] = None  # Resolved from media_id
    supplier_id: Optional[uuid.UUID] = None
    cost_price_ngn: Optional[Decimal] = None
    created_at: datetime
    updated_at: datetime
    images: list[ProductImageResponse] = []  # Include images for list views
    default_variant: Optional[DefaultVariantResponse] = None  # For quick-add on cards
    category: Optional[CategoryResponse] = None  # Category info for list views


class ProductDetail(ProductResponse):
    """Full product detail with variants and images (admin)."""

    variants: list[ProductVariantWithInventory] = []
    images: list[ProductImageResponse] = []
    videos: list[ProductVideoResponse] = []
    category: Optional[CategoryResponse] = None
    supplier_name: Optional[str] = None  # Resolved from supplier relationship


class PublicProductDetail(ProductResponse):
    """Public product detail - hides internal inventory data like quantity_on_hand."""

    variants: list[PublicProductVariantInfo] = []
    images: list[ProductImageResponse] = []
    videos: list[ProductVideoResponse] = []
    category: Optional[CategoryResponse] = None


class ProductListResponse(BaseModel):
    """Paginated product list."""

    items: list[ProductResponse]
    total: int
    page: int
    page_size: int
    total_pages: int
