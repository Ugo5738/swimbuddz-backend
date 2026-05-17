"""Checkout request/response schemas (start, payment init)."""

import uuid
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field

from services.store_service.models import FulfillmentType


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
