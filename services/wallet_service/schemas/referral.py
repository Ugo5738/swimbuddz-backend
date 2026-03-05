"""Referral request/response schemas."""

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class ReferralCodeResponse(BaseModel):
    """Returned when a member requests their referral code."""

    code: str
    share_link: str
    share_text: str
    is_active: bool
    uses_count: int
    successful_referrals: int
    max_uses: Optional[int] = None
    expires_at: Optional[datetime] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ReferralApplyRequest(BaseModel):
    """Apply a referral code during/after registration."""

    code: str


class ReferralApplyResponse(BaseModel):
    success: bool
    message: str


class ReferralStatsResponse(BaseModel):
    total_referrals_sent: int
    registered: int
    qualified: int
    rewarded: int
    pending: int
    total_bubbles_earned: int
    is_ambassador: bool
    referrals_to_ambassador: int
    max_referrals: int
    remaining_referrals: int


class ReferralHistoryItem(BaseModel):
    id: uuid.UUID
    referee_auth_id: str
    referee_name: Optional[str] = None
    status: str
    referrer_reward_bubbles: Optional[int] = None
    referee_reward_bubbles: Optional[int] = None
    referral_code: Optional[str] = None
    qualification_trigger: Optional[str] = None
    referee_registered_at: Optional[datetime] = None
    qualified_at: Optional[datetime] = None
    rewarded_at: Optional[datetime] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ReferralCodeValidateResponse(BaseModel):
    """Returned when validating a referral code (public, no auth required)."""

    valid: bool
    code: str | None = None
    message: str | None = None


class AdminReferralListResponse(BaseModel):
    items: list[ReferralHistoryItem]
    total: int
    skip: int
    limit: int


class AdminReferralProgramStats(BaseModel):
    total_codes_generated: int
    total_registrations: int
    total_qualified: int
    total_rewarded: int
    conversion_rate: float
    total_bubbles_distributed: int
