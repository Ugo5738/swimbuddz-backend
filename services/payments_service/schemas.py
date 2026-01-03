import enum
import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field
from services.payments_service.models import PaymentMethod, PaymentPurpose, PaymentStatus


class ClubBillingCycle(str, enum.Enum):
    QUARTERLY = "quarterly"
    BIANNUAL = "biannual"
    ANNUAL = "annual"


class CreatePaymentIntentRequest(BaseModel):
    purpose: PaymentPurpose
    currency: str = Field(default="NGN", min_length=3, max_length=8)
    payment_method: str = Field(default="paystack")  # paystack or manual_transfer

    years: int = Field(default=1, ge=1, le=5)
    months: int = Field(default=1, ge=1, le=24)
    club_billing_cycle: Optional[ClubBillingCycle] = None

    cohort_id: Optional[uuid.UUID] = None
    enrollment_id: Optional[uuid.UUID] = None  # For ACADEMY_COHORT payments
    discount_code: Optional[str] = None  # Optional discount code
    include_community_extension: bool = (
        False  # Include Community extension if Club exceeds
    )
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
    # Discount info
    original_amount: Optional[float] = None  # Amount before discount (if applied)
    discount_applied: Optional[float] = None  # Discount amount
    discount_code: Optional[str] = None
    # Community extension info (for Club payments)
    requires_community_extension: bool = False
    community_extension_months: int = 0
    community_extension_amount: float = 0
    total_with_extension: Optional[float] = None

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
    payment_method: Optional[str] = None  # paystack or manual_transfer
    proof_of_payment_url: Optional[str] = None  # URL for uploaded proof
    admin_review_note: Optional[str] = None  # Note from admin review
    paid_at: Optional[datetime] = None
    entitlement_applied_at: Optional[datetime] = None
    entitlement_error: Optional[str] = None
    payment_metadata: Optional[dict] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CompletePaymentRequest(BaseModel):
    provider: str = Field(default="paystack", min_length=2, max_length=32)
    provider_reference: Optional[str] = Field(default=None, max_length=128)
    paid_at: Optional[datetime] = None
    note: Optional[str] = Field(default=None, max_length=500)


class SubmitProofRequest(BaseModel):
    """Submit proof of payment for manual transfer."""
    proof_url: str = Field(..., max_length=512)  # URL of uploaded proof image


class AdminReviewRequest(BaseModel):
    """Admin review action for a manual payment."""
    note: Optional[str] = Field(default=None, max_length=500)


# --- Discount Schemas ---


class DiscountCreate(BaseModel):
    code: str = Field(..., min_length=2, max_length=50)
    description: Optional[str] = None
    discount_type: str = Field(..., pattern="^(percentage|fixed)$")
    value: float = Field(..., gt=0)
    applies_to: Optional[list[str]] = None  # ["COMMUNITY", "CLUB", "ACADEMY_COHORT"]
    valid_from: Optional[datetime] = None
    valid_until: Optional[datetime] = None
    max_uses: Optional[int] = Field(default=None, ge=1)
    max_uses_per_user: Optional[int] = Field(default=None, ge=1)
    is_active: bool = True


class DiscountUpdate(BaseModel):
    description: Optional[str] = None
    discount_type: Optional[str] = Field(default=None, pattern="^(percentage|fixed)$")
    value: Optional[float] = Field(default=None, gt=0)
    applies_to: Optional[list[str]] = None
    valid_from: Optional[datetime] = None
    valid_until: Optional[datetime] = None
    max_uses: Optional[int] = None
    max_uses_per_user: Optional[int] = None
    is_active: Optional[bool] = None


class DiscountResponse(BaseModel):
    id: uuid.UUID
    code: str
    description: Optional[str] = None
    discount_type: str
    value: float
    applies_to: Optional[list[str]] = None
    valid_from: Optional[datetime] = None
    valid_until: Optional[datetime] = None
    max_uses: Optional[int] = None
    current_uses: int
    max_uses_per_user: Optional[int] = None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PricingConfigResponse(BaseModel):
    """Public pricing configuration for frontend display."""

    community_annual: int
    club_quarterly: int
    club_biannual: int
    club_annual: int
    currency: str = "NGN"
