"""Pydantic schemas for CorporateProgram."""

import uuid
from datetime import date as _Date
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from services.corporate_service.models.enums import (
    DiscountTier,
    PaymentTerms,
    ProgramStatus,
)


class CorporateProgramBase(BaseModel):
    name: str = Field(..., max_length=255)
    employee_count: int = Field(0, ge=0)
    discount_tier: DiscountTier = DiscountTier.FULL_PRICE
    per_employee_kobo: int = Field(..., ge=0)
    total_kobo: int = Field(..., ge=0)
    payment_terms: PaymentTerms = PaymentTerms.DEPOSIT_HALF
    deposit_paid_kobo: int = Field(0, ge=0)
    balance_paid_kobo: int = Field(0, ge=0)
    expected_start_date: Optional[_Date] = None
    expected_end_date: Optional[_Date] = None
    is_pilot_partner: bool = False
    notes: Optional[str] = None


class CorporateProgramCreate(CorporateProgramBase):
    contact_id: uuid.UUID
    deal_id: Optional[uuid.UUID] = None
    status: ProgramStatus = ProgramStatus.DRAFT


class CorporateProgramUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=255)
    status: Optional[ProgramStatus] = None
    employee_count: Optional[int] = Field(None, ge=0)
    discount_tier: Optional[DiscountTier] = None
    per_employee_kobo: Optional[int] = Field(None, ge=0)
    total_kobo: Optional[int] = Field(None, ge=0)
    payment_terms: Optional[PaymentTerms] = None
    deposit_paid_kobo: Optional[int] = Field(None, ge=0)
    balance_paid_kobo: Optional[int] = Field(None, ge=0)
    expected_start_date: Optional[_Date] = None
    actual_start_date: Optional[_Date] = None
    expected_end_date: Optional[_Date] = None
    actual_end_date: Optional[_Date] = None
    is_pilot_partner: Optional[bool] = None
    notes: Optional[str] = None


class CorporateProgramResponse(CorporateProgramBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    contact_id: uuid.UUID
    deal_id: Optional[uuid.UUID] = None
    status: ProgramStatus
    cohort_id: Optional[uuid.UUID] = None
    corporate_wallet_id: Optional[uuid.UUID] = None
    actual_start_date: Optional[_Date] = None
    actual_end_date: Optional[_Date] = None
    created_at: datetime
    updated_at: datetime


class CorporateProgramListResponse(BaseModel):
    items: list[CorporateProgramResponse]
    total: int
    page: int
    page_size: int


class LinkCohortRequest(BaseModel):
    cohort_id: uuid.UUID


class ProvisionWalletRequest(BaseModel):
    """Optional override for wallet budget; defaults to program total_kobo."""

    budget_kobo: Optional[int] = Field(None, ge=0)
    member_bubble_limit: Optional[int] = Field(None, ge=0)


class EnrollAllResponse(BaseModel):
    enrolled: int
    skipped_no_member_id: int
    skipped_already_booked: int
    employee_count: int
