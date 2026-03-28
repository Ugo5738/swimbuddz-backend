"""Pydantic schemas for store service."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from services.store_service.models import (
    CartStatus,
    FulfillmentType,
    OrderStatus,
    PayoutStatus,
    ProductStatus,
    ProductType,
    SourcingType,
    StoreCreditSourceType,
    SupplierStatus,
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


class InventoryVariantProduct(BaseModel):
    """Minimal product info nested in inventory variant."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str


class InventoryVariantInfo(BaseModel):
    """Variant info included in inventory responses."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    sku: str
    name: Optional[str] = None
    product: Optional[InventoryVariantProduct] = None


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
    variant: Optional[InventoryVariantInfo] = None


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
    pool_id: Optional[uuid.UUID] = None  # Soft reference to pools_service
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
    pool_id: Optional[uuid.UUID] = None
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
    bubbles_to_apply: Optional[int] = Field(
        None,
        ge=0,
        description=(
            "Number of Bubbles to apply toward payment. "
            "If omitted or 0, no Bubbles are used. "
            "If Bubbles cover the full amount, no Paystack payment is required."
        ),
    )


class CheckoutStartResponse(BaseModel):
    """Checkout started, pending payment."""

    order_id: uuid.UUID
    order_number: str
    total_ngn: Decimal
    delivery_fee_ngn: Decimal
    requires_payment: bool  # False if total is 0 (all store credit / bubbles)
    bubbles_applied: Optional[int] = None  # Bubbles debited from wallet (if any)
    bubbles_amount_ngn: Optional[Decimal] = None  # NGN value of applied Bubbles
    paystack_amount_ngn: Optional[Decimal] = None  # Remaining for Paystack (if any)


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
    supplier_id: Optional[uuid.UUID] = None
    supplier_name: Optional[str] = None


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
    bubbles_applied: Optional[int] = None  # Bubbles debited from wallet (if any)
    wallet_transaction_id: Optional[str] = None
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


# ============================================================================
# SUPPLIER SCHEMAS
# ============================================================================


class SupplierBase(BaseModel):
    name: str = Field(..., max_length=255)
    slug: str = Field(..., max_length=255)
    contact_name: Optional[str] = Field(None, max_length=255)
    contact_email: Optional[str] = Field(None, max_length=255)
    contact_phone: Optional[str] = Field(None, max_length=50)
    description: Optional[str] = None
    commission_percent: Optional[Decimal] = Field(None, ge=0, le=100)
    payout_bank_name: Optional[str] = Field(None, max_length=255)
    payout_account_number: Optional[str] = Field(None, max_length=50)
    payout_account_name: Optional[str] = Field(None, max_length=255)
    is_verified: bool = False
    status: SupplierStatus = SupplierStatus.ACTIVE
    is_active: bool = True


class SupplierCreate(SupplierBase):
    pass


class SupplierUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=255)
    slug: Optional[str] = Field(None, max_length=255)
    contact_name: Optional[str] = Field(None, max_length=255)
    contact_email: Optional[str] = Field(None, max_length=255)
    contact_phone: Optional[str] = Field(None, max_length=50)
    description: Optional[str] = None
    commission_percent: Optional[Decimal] = Field(None, ge=0, le=100)
    payout_bank_name: Optional[str] = Field(None, max_length=255)
    payout_account_number: Optional[str] = Field(None, max_length=50)
    payout_account_name: Optional[str] = Field(None, max_length=255)
    is_verified: Optional[bool] = None
    status: Optional[SupplierStatus] = None
    is_active: Optional[bool] = None


class SupplierResponse(SupplierBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    probation_ends_at: Optional[datetime] = None
    total_products: int = 0
    total_orders: int = 0
    average_fulfillment_hours: Optional[Decimal] = None
    created_at: datetime
    updated_at: datetime


class SupplierListResponse(BaseModel):
    """Paginated supplier list."""

    items: list[SupplierResponse]
    total: int
    page: int
    page_size: int


# ============================================================================
# SUPPLIER PAYOUT SCHEMAS
# ============================================================================


class SupplierPayoutCreate(BaseModel):
    """Create a payout record for a supplier."""

    payout_period_start: date
    payout_period_end: date
    total_sales_ngn: Decimal = Field(..., ge=0)
    commission_ngn: Decimal = Field(..., ge=0)
    payout_amount_ngn: Decimal = Field(..., ge=0)
    notes: Optional[str] = None


class SupplierPayoutStatusUpdate(BaseModel):
    """Update payout status (e.g., pending → processing → paid)."""

    status: PayoutStatus
    payment_reference: Optional[str] = Field(None, max_length=255)
    notes: Optional[str] = None


class SupplierPayoutResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    supplier_id: uuid.UUID
    payout_period_start: date
    payout_period_end: date
    total_sales_ngn: Decimal
    commission_ngn: Decimal
    payout_amount_ngn: Decimal
    status: PayoutStatus
    paid_at: Optional[datetime] = None
    payment_reference: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime


class SupplierPayoutListResponse(BaseModel):
    """Paginated payout list."""

    items: list[SupplierPayoutResponse]
    total: int
    page: int
    page_size: int


# ============================================================================
# BUNDLE / KIT SCHEMAS
# ============================================================================


class BundleItemCreate(BaseModel):
    """Add a component product to a bundle."""

    component_product_id: uuid.UUID
    component_variant_id: Optional[uuid.UUID] = None
    quantity: int = Field(1, ge=1)
    sort_order: int = 0


class BundleItemResponse(BaseModel):
    """Bundle component with resolved product info."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    component_product_id: uuid.UUID
    component_variant_id: Optional[uuid.UUID] = None
    quantity: int
    sort_order: int

    # Resolved from relationships
    component_name: Optional[str] = None
    component_slug: Optional[str] = None
    component_image_url: Optional[str] = None
    component_price_ngn: Optional[Decimal] = None


class BundleCreate(ProductCreate):
    """Create a bundle product with its component items."""

    product_type: ProductType = ProductType.BUNDLE
    bundle_items: list[BundleItemCreate] = Field(..., min_length=1)


class BundleUpdate(ProductUpdate):
    """Update a bundle product. Bundle items managed separately."""

    pass


class BundleDetailResponse(ProductDetail):
    """Bundle product detail with component items and savings info."""

    bundle_items: list[BundleItemResponse] = []
    total_individual_price_ngn: Optional[Decimal] = None
    savings_percent: Optional[Decimal] = None
