"""Order schemas (line items, full order, list, status updates)."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from services.store_service.models import (
    FulfillmentType,
    OrderFulfillmentStatus,
    OrderPaymentStatus,
    OrderStatus,
)

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
    net_line_total_ngn: Optional[Decimal] = None
    is_preorder: bool
    sourcing_type: str = "stocked"
    requires_size_chart_ack: bool = False
    supplier_committed_at: Optional[datetime] = None
    estimated_ship_date: Optional[datetime]
    supplier_id: Optional[uuid.UUID] = None
    supplier_name: Optional[str] = None
    variant: Optional[OrderItemVariantInfo] = None


class OrderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    order_number: str
    status: OrderStatus
    payment_status: Optional[OrderPaymentStatus] = None
    fulfillment_status: Optional[OrderFulfillmentStatus] = None
    fulfillment_type: FulfillmentType

    customer_email: str
    customer_name: str
    customer_phone: Optional[str]

    subtotal_ngn: Decimal
    discount_amount_ngn: Decimal
    store_credit_applied_ngn: Decimal
    delivery_fee_ngn: Decimal
    order_total_ngn: Optional[Decimal] = None
    cash_payable_ngn: Optional[Decimal] = None
    total_ngn: Decimal

    discount_code: Optional[str]
    discount_breakdown: Optional[dict]

    pickup_location_id: Optional[uuid.UUID]
    delivery_address: Optional[dict]
    delivery_notes: Optional[str]
    customer_notes: Optional[str]
    admin_notes: Optional[str] = None

    payment_reference: Optional[str]
    bubbles_applied: Optional[int] = None  # Bubbles debited from wallet (if any)
    wallet_transaction_id: Optional[str] = None
    wallet_hold_id: Optional[str] = None
    paid_at: Optional[datetime]
    shipped_at: Optional[datetime] = None
    delivered_at: Optional[datetime] = None
    fulfilled_at: Optional[datetime]
    cancelled_at: Optional[datetime]
    refunded_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    items: list[OrderItemResponse] = []
    pickup_location: Optional[PickupLocationResponse] = None

    @model_validator(mode="after")
    def _populate_derived_fields(self) -> "OrderResponse":
        if self.order_total_ngn is None:
            self.order_total_ngn = max(
                Decimal("0"),
                self.subtotal_ngn - self.discount_amount_ngn + self.delivery_fee_ngn,
            )
        if self.cash_payable_ngn is None:
            self.cash_payable_ngn = self.total_ngn
        if self.payment_status is None:
            payment_map = {
                OrderStatus.PENDING_PAYMENT: OrderPaymentStatus.UNPAID,
                OrderStatus.PAYMENT_FAILED: OrderPaymentStatus.FAILED,
                OrderStatus.REFUNDED: OrderPaymentStatus.REFUNDED,
                OrderStatus.CANCELLED: (
                    OrderPaymentStatus.PAID if self.paid_at else OrderPaymentStatus.UNPAID
                ),
            }
            self.payment_status = payment_map.get(self.status, OrderPaymentStatus.PAID)
        if self.fulfillment_status is None:
            fulfillment_map = {
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
            self.fulfillment_status = fulfillment_map.get(
                self.status, OrderFulfillmentStatus.NOT_STARTED
            )
        return self


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
