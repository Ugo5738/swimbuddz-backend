"""Supplier schemas."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from services.store_service.models import SupplierStatus


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
