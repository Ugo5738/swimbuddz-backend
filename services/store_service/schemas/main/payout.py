"""Supplier payout schemas."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from services.store_service.models import PayoutStatus


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
