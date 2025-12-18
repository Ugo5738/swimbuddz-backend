import enum
import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field
from services.payments_service.models import PaymentPurpose, PaymentStatus


class ClubBillingCycle(str, enum.Enum):
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    BIANNUAL = "biannual"
    ANNUAL = "annual"


class CreatePaymentIntentRequest(BaseModel):
    purpose: PaymentPurpose
    currency: str = Field(default="NGN", min_length=3, max_length=8)

    years: int = Field(default=1, ge=1, le=5)
    months: int = Field(default=1, ge=1, le=24)
    club_billing_cycle: Optional[ClubBillingCycle] = None

    cohort_id: Optional[uuid.UUID] = None
    # Accept "metadata" for backwards-compat, store internally as payment_metadata.
    payment_metadata: Optional[dict] = Field(default=None, alias="metadata")

    model_config = ConfigDict(populate_by_name=True)


class PaymentIntentResponse(BaseModel):
    reference: str
    amount: float
    currency: str
    purpose: PaymentPurpose
    status: PaymentStatus
    checkout_url: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PaymentResponse(BaseModel):
    id: uuid.UUID
    reference: str
    member_auth_id: str
    payer_email: Optional[EmailStr] = None
    purpose: PaymentPurpose
    amount: float
    currency: str
    status: PaymentStatus
    provider: Optional[str] = None
    provider_reference: Optional[str] = None
    paid_at: Optional[datetime] = None
    entitlement_applied_at: Optional[datetime] = None
    entitlement_error: Optional[str] = None
    payment_metadata: Optional[dict] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CompletePaymentRequest(BaseModel):
    provider: str = Field(default="manual", min_length=2, max_length=32)
    provider_reference: Optional[str] = Field(default=None, max_length=128)
    paid_at: Optional[datetime] = None
    note: Optional[str] = Field(default=None, max_length=500)
