"""Payout schemas for coach payouts."""

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field
from services.payments_service.models import PayoutMethod, PayoutStatus


class PayoutCreate(BaseModel):
    """Schema for creating a coach payout."""

    coach_member_id: uuid.UUID
    period_start: datetime
    period_end: datetime
    period_label: str = Field(..., min_length=1, max_length=50)

    academy_earnings: int = Field(default=0, ge=0)
    session_earnings: int = Field(default=0, ge=0)
    other_earnings: int = Field(default=0, ge=0)

    admin_notes: Optional[str] = None


class PayoutApprove(BaseModel):
    """Schema for approving a payout."""

    admin_notes: Optional[str] = None


class PayoutInitiateTransfer(BaseModel):
    """Schema for initiating a Paystack transfer."""

    # No additional fields needed - uses coach's bank account


class PayoutCompleteManual(BaseModel):
    """Schema for marking a payout as manually paid."""

    payout_method: PayoutMethod = PayoutMethod.BANK_TRANSFER
    payment_reference: str = Field(..., min_length=1, max_length=100)
    admin_notes: Optional[str] = None


class PayoutFail(BaseModel):
    """Schema for marking a payout as failed."""

    failure_reason: str = Field(..., min_length=1)
    admin_notes: Optional[str] = None


class PayoutResponse(BaseModel):
    """Response for a coach payout."""

    id: str
    coach_member_id: str

    period_start: datetime
    period_end: datetime
    period_label: str

    academy_earnings: int
    session_earnings: int
    other_earnings: int
    total_amount: int
    currency: str

    status: PayoutStatus
    payout_method: Optional[PayoutMethod] = None

    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None

    paid_at: Optional[datetime] = None
    payment_reference: Optional[str] = None
    paystack_transfer_code: Optional[str] = None
    paystack_transfer_status: Optional[str] = None

    admin_notes: Optional[str] = None
    failure_reason: Optional[str] = None

    created_at: datetime
    updated_at: datetime


class PayoutListResponse(BaseModel):
    """Paginated list of payouts."""

    items: list[PayoutResponse]
    total: int
    page: int
    page_size: int


class PayoutSummary(BaseModel):
    """Summary stats for payouts."""

    total_pending: int
    total_approved: int
    total_paid: int
    total_failed: int
    pending_amount: int  # in kobo
    paid_amount: int  # in kobo
