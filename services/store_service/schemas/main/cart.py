"""Cart schemas (items, discount, totals)."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from services.store_service.models import CartStatus


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
