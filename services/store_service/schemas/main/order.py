"""Order schemas (line items, full order, list, status updates)."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from services.store_service.models import FulfillmentType, OrderStatus

from .pickup_location import PickupLocationResponse


class OrderItemImageInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    url: str
    is_primary: bool = False


class OrderItemProductInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    images: list[OrderItemImageInfo] = []


class OrderItemVariantInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    sku: str
    name: Optional[str] = None
    options: dict = Field(default_factory=dict)
    product: Optional[OrderItemProductInfo] = None


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
    variant: Optional[OrderItemVariantInfo] = None


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


class OrderUpdate(BaseModel):
    """Partial admin update for an order."""

    status: Optional[OrderStatus] = None
    admin_notes: Optional[str] = None
    tracking_number: Optional[str] = None
