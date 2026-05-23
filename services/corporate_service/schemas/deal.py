"""Pydantic schemas for CorporateDeal."""

import uuid
from datetime import date as _Date
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from services.corporate_service.models.enums import (
    DealLostReason,
    DealStage,
    DiscountTier,
)


class CorporateDealBase(BaseModel):
    title: str = Field(..., max_length=255)
    stage: DealStage = DealStage.LEAD
    expected_employees: Optional[int] = Field(None, ge=0)
    expected_discount_tier: Optional[DiscountTier] = None
    expected_total_kobo: Optional[int] = Field(None, ge=0)
    expected_close_date: Optional[_Date] = None
    next_action: Optional[str] = None
    next_action_due: Optional[_Date] = None
    owner_auth_id: Optional[str] = Field(None, max_length=255)
    notes: Optional[str] = None


class CorporateDealCreate(CorporateDealBase):
    pass


class CorporateDealUpdate(BaseModel):
    title: Optional[str] = Field(None, max_length=255)
    stage: Optional[DealStage] = None
    expected_employees: Optional[int] = Field(None, ge=0)
    expected_discount_tier: Optional[DiscountTier] = None
    expected_total_kobo: Optional[int] = Field(None, ge=0)
    expected_close_date: Optional[_Date] = None
    actual_close_date: Optional[_Date] = None
    next_action: Optional[str] = None
    next_action_due: Optional[_Date] = None
    last_touch_at: Optional[datetime] = None
    lost_reason: Optional[DealLostReason] = None
    lost_notes: Optional[str] = None
    owner_auth_id: Optional[str] = Field(None, max_length=255)
    notes: Optional[str] = None


class CorporateDealResponse(CorporateDealBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    contact_id: uuid.UUID
    actual_close_date: Optional[_Date] = None
    last_touch_at: Optional[datetime] = None
    lost_reason: Optional[DealLostReason] = None
    lost_notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class CorporateDealListResponse(BaseModel):
    items: list[CorporateDealResponse]
    total: int
    page: int
    page_size: int


class CorporateDealWinRequest(BaseModel):
    """Promote a deal to a CorporateProgram.

    Optional fields override what the deal already records; required fields
    are the bits a deal doesn't carry (program name + payment terms choice).
    """

    program_name: str = Field(..., max_length=255)
    employee_count: int = Field(..., gt=0)
    discount_tier: DiscountTier
    payment_terms: Optional[str] = None  # PaymentTerms enum value
    is_pilot_partner: bool = False
    expected_start_date: Optional[_Date] = None
    expected_end_date: Optional[_Date] = None
    notes: Optional[str] = None


class CorporateDealLossRequest(BaseModel):
    """Close a deal as lost."""

    lost_reason: DealLostReason
    lost_notes: Optional[str] = None
