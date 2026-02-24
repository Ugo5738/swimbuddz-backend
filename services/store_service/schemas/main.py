"""Pydantic schemas for store service."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field
from services.store_service.models import (
    CartStatus,
    FulfillmentType,
    OrderStatus,
    ProductStatus,
    SourcingType,
    StoreCreditSourceType,
)

# ============================================================================
# CATEGORY SCHEMAS
# ============================================================================


class CategoryBase(BaseModel):
    name: str = Field(..., max_length=100)
    slug: str = Field(..., max_length=100)
    description: Optional[str] = None
    image_media_id: Optional[uuid.UUID] = None
    parent_id: Optional[uuid.UUID] = None
    sort_order: int = 0
    is_active: bool = True


class CategoryCreate(CategoryBase):
    pass


class CategoryUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=100)
    slug: Optional[str] = Field(None, max_length=100)
    description: Optional[str] = None
    image_media_id: Optional[uuid.UUID] = None
    parent_id: Optional[uuid.UUID] = None
    sort_order: Optional[int] = None
    is_active: Optional[bool] = None


class CategoryResponse(CategoryBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    image_url: Optional[str] = None  # Resolved from media_id
    created_at: datetime
    updated_at: datetime


class CategoryWithChildren(CategoryResponse):
    children: list["CategoryWithChildren"] = []


# ============================================================================
# PRODUCT SCHEMAS
# ============================================================================


class ProductBase(BaseModel):
    name: str = Field(..., max_length=255)
    slug: str = Field(..., max_length=255)
    category_id: Optional[uuid.UUID] = None
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


class ProductCreate(ProductBase):
    pass


class ProductUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=255)
    slug: Optional[str] = Field(None, max_length=255)
    category_id: Optional[uuid.UUID] = None
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


class ProductVariantBase(BaseModel):
    sku: str = Field(..., max_length=100)
    name: Optional[str] = Field(None, max_length=255)
    options: dict = Field(default_factory=dict)
    price_override_ngn: Optional[Decimal] = Field(None, ge=0)
    weight_grams: Optional[int] = Field(None, ge=0)
    is_active: bool = True


class ProductVariantCreate(ProductVariantBase):
    pass


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
    quantity_available: int = 0
    quantity_on_hand: int = 0


class ProductImageBase(BaseModel):
    url: str = Field(..., max_length=512)
    alt_text: Optional[str] = Field(None, max_length=255)
    sort_order: int = 0
    is_primary: bool = False
    variant_id: Optional[uuid.UUID] = None


class ProductImageCreate(ProductImageBase):
    pass


class ProductImageResponse(ProductImageBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product_id: uuid.UUID
    created_at: datetime


class ProductResponse(ProductBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    size_chart_url: Optional[str] = None  # Resolved from media_id
    created_at: datetime
    updated_at: datetime
    images: list[ProductImageResponse] = []  # Include images for list views


class ProductDetail(ProductResponse):
    """Full product detail with variants and images."""

    variants: list[ProductVariantWithInventory] = []
    images: list[ProductImageResponse] = []
    category: Optional[CategoryResponse] = None


class ProductListResponse(BaseModel):
    """Paginated product list."""

    items: list[ProductResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


# ============================================================================
# COLLECTION SCHEMAS
# ============================================================================


class CollectionBase(BaseModel):
    name: str = Field(..., max_length=100)
    slug: str = Field(..., max_length=100)
    description: Optional[str] = None
    image_media_id: Optional[uuid.UUID] = None
    is_active: bool = True
    sort_order: int = 0


class CollectionCreate(CollectionBase):
    pass


class CollectionUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=100)
    slug: Optional[str] = Field(None, max_length=100)
    description: Optional[str] = None
    image_media_id: Optional[uuid.UUID] = None
    is_active: Optional[bool] = None
    sort_order: Optional[int] = None


class CollectionResponse(CollectionBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    image_url: Optional[str] = None  # Resolved from media_id
    created_at: datetime
    updated_at: datetime


class CollectionWithProducts(CollectionResponse):
    products: list[ProductResponse] = []


# ============================================================================
# INVENTORY SCHEMAS
# ============================================================================


class InventoryItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    variant_id: uuid.UUID
    quantity_on_hand: int
    quantity_reserved: int
    quantity_available: int
    low_stock_threshold: int
    last_restock_at: Optional[datetime]
    last_sold_at: Optional[datetime]


class InventoryAdjustment(BaseModel):
    """Adjust inventory (restock, correction, etc.)."""

    quantity: int = Field(..., description="Positive to add, negative to subtract")
    notes: Optional[str] = None


class LowStockItem(BaseModel):
    """Low stock alert item."""

    variant_id: uuid.UUID
    sku: str
    product_name: str
    variant_name: Optional[str]
    quantity_on_hand: int
    quantity_available: int
    low_stock_threshold: int


# ============================================================================
# CART SCHEMAS
# ============================================================================


class CartItemCreate(BaseModel):
    variant_id: uuid.UUID
    quantity: int = Field(1, ge=1)


class CartItemUpdate(BaseModel):
    quantity: int = Field(..., ge=1)


class CartItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    variant_id: uuid.UUID
    quantity: int
    unit_price_ngn: Decimal

    # Enriched from variant
    product_name: Optional[str] = None
    variant_name: Optional[str] = None
    sku: Optional[str] = None
    image_url: Optional[str] = None


class ApplyDiscountRequest(BaseModel):
    code: str = Field(..., max_length=50)


class CartResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: CartStatus
    discount_code: Optional[str]
    member_discount_percent: Optional[Decimal]
    expires_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime

    items: list[CartItemResponse] = []

    # Calculated totals
    subtotal_ngn: Decimal = Decimal("0")
    discount_amount_ngn: Decimal = Decimal("0")
    total_ngn: Decimal = Decimal("0")


# ============================================================================
# PICKUP LOCATION SCHEMAS
# ============================================================================


class PickupLocationBase(BaseModel):
    name: str = Field(..., max_length=100)
    address: Optional[str] = Field(None, max_length=500)
    description: Optional[str] = None
    contact_phone: Optional[str] = Field(None, max_length=50)
    contact_email: Optional[str] = Field(None, max_length=255)
    is_active: bool = True
    sort_order: int = 0


class PickupLocationCreate(PickupLocationBase):
    pass


class PickupLocationUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=100)
    address: Optional[str] = Field(None, max_length=500)
    description: Optional[str] = None
    contact_phone: Optional[str] = Field(None, max_length=50)
    contact_email: Optional[str] = Field(None, max_length=255)
    is_active: Optional[bool] = None
    sort_order: Optional[int] = None


class PickupLocationResponse(PickupLocationBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    updated_at: datetime


# ============================================================================
# CHECKOUT SCHEMAS
# ============================================================================


class DeliveryAddress(BaseModel):
    """Delivery address for home delivery."""

    street: str = Field(..., max_length=255)
    city: str = Field(..., max_length=100)
    state: str = Field(..., max_length=100)
    phone: str = Field(..., max_length=50)
    additional_info: Optional[str] = None


class CheckoutStartRequest(BaseModel):
    """Start checkout process."""

    fulfillment_type: FulfillmentType = FulfillmentType.PICKUP
    pickup_location_id: Optional[uuid.UUID] = None
    delivery_address: Optional[DeliveryAddress] = None
    customer_notes: Optional[str] = None
    size_chart_acknowledged: bool = False  # Required if any product needs it
    apply_store_credit: bool = False  # Apply available store credit to reduce total


class CheckoutStartResponse(BaseModel):
    """Checkout started, pending payment."""

    order_id: uuid.UUID
    order_number: str
    total_ngn: Decimal
    delivery_fee_ngn: Decimal
    requires_payment: bool  # False if total is 0 (all store credit)


class PaymentInitRequest(BaseModel):
    """Initialize payment for order."""

    order_id: uuid.UUID


class PaymentInitResponse(BaseModel):
    """Payment initialization response."""

    payment_reference: str
    authorization_url: str
    access_code: str


# ============================================================================
# ORDER SCHEMAS
# ============================================================================


class OrderItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    variant_id: uuid.UUID
    product_name: str
    variant_name: Optional[str]
    sku: str
    quantity: int
    unit_price_ngn: Decimal
    line_total_ngn: Decimal
    is_preorder: bool
    estimated_ship_date: Optional[datetime]


class OrderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    order_number: str
    status: OrderStatus
    fulfillment_type: FulfillmentType

    customer_email: str
    customer_name: str
    customer_phone: Optional[str]

    subtotal_ngn: Decimal
    discount_amount_ngn: Decimal
    store_credit_applied_ngn: Decimal
    delivery_fee_ngn: Decimal
    total_ngn: Decimal

    discount_code: Optional[str]
    discount_breakdown: Optional[dict]

    pickup_location_id: Optional[uuid.UUID]
    delivery_address: Optional[dict]
    delivery_notes: Optional[str]
    customer_notes: Optional[str]

    payment_reference: Optional[str]
    paid_at: Optional[datetime]
    fulfilled_at: Optional[datetime]
    cancelled_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime

    items: list[OrderItemResponse] = []
    pickup_location: Optional[PickupLocationResponse] = None


class OrderListResponse(BaseModel):
    """Paginated order list."""

    items: list[OrderResponse]
    total: int
    page: int
    page_size: int


class OrderStatusUpdate(BaseModel):
    """Update order status (admin)."""

    status: OrderStatus
    admin_notes: Optional[str] = None


# ============================================================================
# STORE CREDIT SCHEMAS
# ============================================================================


class StoreCreditCreate(BaseModel):
    """Issue store credit (admin)."""

    member_auth_id: str
    amount_ngn: Decimal = Field(..., gt=0)
    source_type: StoreCreditSourceType = StoreCreditSourceType.ADMIN
    source_order_id: Optional[uuid.UUID] = None
    reason: Optional[str] = None
    expires_at: Optional[datetime] = None


class StoreCreditResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    member_auth_id: str
    amount_ngn: Decimal
    balance_ngn: Decimal
    source_type: StoreCreditSourceType
    source_order_id: Optional[uuid.UUID]
    reason: Optional[str]
    expires_at: Optional[datetime]
    issued_by: Optional[str]
    created_at: datetime


class MemberStoreCreditSummary(BaseModel):
    """Summary of member's store credits."""

    total_balance_ngn: Decimal
    credits: list[StoreCreditResponse]
