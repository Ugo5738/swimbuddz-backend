"""Grant and scholarship schemas."""

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field
from services.wallet_service.models.enums import GrantType


class GrantWelcomeBonusRequest(BaseModel):
    """Called by members service after paid membership activation."""

    member_id: uuid.UUID
    member_auth_id: str
    eligible: bool = True
    reason: Optional[str] = None
    granted_by: str = "system"


class GrantWelcomeBonusResponse(BaseModel):
    success: bool
    wallet_id: uuid.UUID
    bonus_granted: bool
    bubbles_awarded: int


class AdminScholarshipCreditRequest(BaseModel):
    """Admin-initiated wallet credit for scholarship or discount deposits.

    Called by academy_service admin endpoints (or directly by admin tooling)
    to deposit Bubbles that cover part or all of a student's installment fees.
    The credit is applied immediately and the idempotency_key ensures that
    retries do not double-credit the same grant.
    """

    member_auth_id: str
    amount: int = Field(..., gt=0, description="Amount in Bubbles (kobo-unit integers)")
    idempotency_key: str = Field(
        ...,
        description=(
            "Unique key for this credit operation. "
            "Recommended format: scholarship-{enrollment_id}-{reason_slug}"
        ),
    )
    reason: str = Field(
        ..., min_length=3, description="Reason for credit (e.g. 'scholarship_50pct')"
    )
    grant_type: GrantType = GrantType.SCHOLARSHIP
    enrollment_id: Optional[str] = None  # For audit trail linking


class GrantPromotionalRequest(BaseModel):
    member_auth_id: str
    bubbles_amount: int = Field(..., gt=0)
    grant_type: GrantType
    reason: str = Field(..., min_length=5)
    campaign_code: Optional[str] = None
    expires_in_days: Optional[int] = Field(
        default=None,
        description="Days until expiry. Null = never expires.",
    )


class BulkGrantPromotionalRequest(BaseModel):
    member_auth_ids: list[str]
    bubbles_amount: int = Field(..., gt=0)
    grant_type: GrantType
    reason: str = Field(..., min_length=5)
    campaign_code: Optional[str] = None
    expires_in_days: Optional[int] = None


class GrantResponse(BaseModel):
    id: uuid.UUID
    wallet_id: uuid.UUID
    member_auth_id: str
    grant_type: GrantType
    bubbles_amount: int
    reason: str
    campaign_code: Optional[str] = None
    expires_at: Optional[datetime] = None
    bubbles_remaining: int
    transaction_id: Optional[uuid.UUID] = None
    granted_by: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class GrantListResponse(BaseModel):
    grants: list[GrantResponse]
    total: int
    skip: int
    limit: int
