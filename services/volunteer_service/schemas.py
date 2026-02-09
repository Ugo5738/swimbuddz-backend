"""Pydantic schemas for the Volunteer Service."""

import uuid
from datetime import date, datetime, time
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field
from services.volunteer_service.models import (
    OpportunityStatus,
    OpportunityType,
    RecognitionTier,
    RewardType,
    SlotStatus,
    VolunteerRoleCategory,
    VolunteerTier,
)

# ============================================================================
# ROLE SCHEMAS
# ============================================================================


class VolunteerRoleBase(BaseModel):
    title: str = Field(..., max_length=120)
    description: Optional[str] = None
    category: VolunteerRoleCategory = VolunteerRoleCategory.OTHER
    required_skills: Optional[list[str]] = None
    min_tier: VolunteerTier = VolunteerTier.TIER_1
    icon: Optional[str] = None
    sort_order: int = 0
    is_active: bool = True


class VolunteerRoleCreate(VolunteerRoleBase):
    pass


class VolunteerRoleUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    category: Optional[VolunteerRoleCategory] = None
    required_skills: Optional[list[str]] = None
    min_tier: Optional[VolunteerTier] = None
    icon: Optional[str] = None
    sort_order: Optional[int] = None
    is_active: Optional[bool] = None


class VolunteerRoleResponse(VolunteerRoleBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    active_volunteers_count: int = 0


# ============================================================================
# PROFILE SCHEMAS
# ============================================================================


class VolunteerProfileCreate(BaseModel):
    preferred_roles: Optional[list[str]] = None
    available_days: Optional[list[str]] = None
    notes: Optional[str] = None


class VolunteerProfileUpdate(BaseModel):
    preferred_roles: Optional[list[str]] = None
    available_days: Optional[list[str]] = None
    notes: Optional[str] = None


class VolunteerProfileAdminUpdate(BaseModel):
    tier: Optional[VolunteerTier] = None
    tier_override: Optional[VolunteerTier] = None
    is_active: Optional[bool] = None
    admin_notes: Optional[str] = None
    reliability_score: Optional[int] = None
    spotlight_quote: Optional[str] = None
    is_featured: Optional[bool] = None
    featured_from: Optional[datetime] = None
    featured_until: Optional[datetime] = None


class VolunteerProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    member_id: uuid.UUID
    tier: VolunteerTier
    tier_override: Optional[VolunteerTier] = None
    total_hours: float
    total_sessions_volunteered: int
    total_no_shows: int
    total_late_cancellations: int
    reliability_score: int
    recognition_tier: Optional[RecognitionTier] = None
    preferred_roles: Optional[list[str]] = None
    available_days: Optional[list[str]] = None
    notes: Optional[str] = None
    is_active: bool
    admin_notes: Optional[str] = None
    spotlight_quote: Optional[str] = None
    is_featured: bool = False
    featured_from: Optional[datetime] = None
    featured_until: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    # Enrichment fields (populated at query time)
    member_name: Optional[str] = None
    member_email: Optional[str] = None


# ============================================================================
# OPPORTUNITY SCHEMAS
# ============================================================================


class VolunteerOpportunityBase(BaseModel):
    title: str = Field(..., max_length=200)
    description: Optional[str] = None
    role_id: Optional[uuid.UUID] = None
    date: date
    start_time: Optional[time] = None
    end_time: Optional[time] = None
    session_id: Optional[uuid.UUID] = None
    event_id: Optional[uuid.UUID] = None
    location_name: Optional[str] = None
    slots_needed: int = 1
    opportunity_type: OpportunityType = OpportunityType.OPEN_CLAIM
    min_tier: VolunteerTier = VolunteerTier.TIER_1
    cancellation_deadline_hours: int = 24


class VolunteerOpportunityCreate(VolunteerOpportunityBase):
    status: OpportunityStatus = OpportunityStatus.DRAFT


class VolunteerOpportunityUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    role_id: Optional[uuid.UUID] = None
    date: Optional[date] = None
    start_time: Optional[time] = None
    end_time: Optional[time] = None
    location_name: Optional[str] = None
    slots_needed: Optional[int] = None
    opportunity_type: Optional[OpportunityType] = None
    status: Optional[OpportunityStatus] = None
    min_tier: Optional[VolunteerTier] = None
    cancellation_deadline_hours: Optional[int] = None


class VolunteerOpportunityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    description: Optional[str] = None
    role_id: Optional[uuid.UUID] = None
    date: date
    start_time: Optional[time] = None
    end_time: Optional[time] = None
    session_id: Optional[uuid.UUID] = None
    event_id: Optional[uuid.UUID] = None
    location_name: Optional[str] = None
    slots_needed: int
    slots_filled: int
    opportunity_type: OpportunityType
    status: OpportunityStatus
    min_tier: VolunteerTier
    cancellation_deadline_hours: int
    created_by: Optional[uuid.UUID] = None
    created_at: datetime
    updated_at: datetime
    # Enrichment
    role_title: Optional[str] = None
    role_category: Optional[str] = None


class VolunteerOpportunityBulkCreate(BaseModel):
    opportunities: list[VolunteerOpportunityCreate]


# ============================================================================
# SLOT SCHEMAS
# ============================================================================


class VolunteerSlotResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    opportunity_id: uuid.UUID
    member_id: uuid.UUID
    status: SlotStatus
    claimed_at: datetime
    approved_at: Optional[datetime] = None
    approved_by: Optional[uuid.UUID] = None
    cancelled_at: Optional[datetime] = None
    cancellation_reason: Optional[str] = None
    checked_in_at: Optional[datetime] = None
    checked_out_at: Optional[datetime] = None
    hours_logged: Optional[float] = None
    admin_notes: Optional[str] = None
    member_feedback: Optional[str] = None
    # Enrichment
    member_name: Optional[str] = None


class VolunteerSlotAdminUpdate(BaseModel):
    status: Optional[SlotStatus] = None
    admin_notes: Optional[str] = None


class CancelSlotRequest(BaseModel):
    reason: Optional[str] = None


class CheckoutSlotRequest(BaseModel):
    hours: Optional[float] = None  # Override auto-calculated hours
    admin_notes: Optional[str] = None


class BulkCompleteRequest(BaseModel):
    slot_ids: list[uuid.UUID]
    hours: Optional[float] = None


# ============================================================================
# HOURS SCHEMAS
# ============================================================================


class VolunteerHoursLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    member_id: uuid.UUID
    slot_id: Optional[uuid.UUID] = None
    opportunity_id: Optional[uuid.UUID] = None
    hours: float
    date: date
    role_id: Optional[uuid.UUID] = None
    source: str
    logged_by: Optional[uuid.UUID] = None
    notes: Optional[str] = None
    created_at: datetime


class ManualHoursCreate(BaseModel):
    member_id: uuid.UUID
    hours: float = Field(..., gt=0, le=24)
    date: date
    role_id: Optional[uuid.UUID] = None
    notes: Optional[str] = None


class HoursSummaryResponse(BaseModel):
    total_hours: float
    total_sessions: int
    hours_this_month: float
    tier: VolunteerTier
    recognition_tier: Optional[RecognitionTier] = None
    reliability_score: int
    next_tier_hours_needed: Optional[float] = None
    by_role: list[dict] = []


# ============================================================================
# REWARD SCHEMAS
# ============================================================================


class VolunteerRewardCreate(BaseModel):
    member_id: uuid.UUID
    reward_type: RewardType
    title: str = Field(..., max_length=200)
    description: Optional[str] = None
    trigger_type: Optional[str] = None
    trigger_value: Optional[str] = None
    expires_at: Optional[datetime] = None
    discount_percent: Optional[int] = None
    discount_amount_ngn: Optional[int] = None


class VolunteerRewardResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    member_id: uuid.UUID
    reward_type: RewardType
    title: str
    description: Optional[str] = None
    trigger_type: Optional[str] = None
    trigger_value: Optional[str] = None
    is_redeemed: bool
    redeemed_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    discount_percent: Optional[int] = None
    discount_amount_ngn: Optional[int] = None
    granted_by: Optional[uuid.UUID] = None
    created_at: datetime


# ============================================================================
# DASHBOARD / LEADERBOARD SCHEMAS
# ============================================================================


class LeaderboardEntry(BaseModel):
    rank: int
    member_id: uuid.UUID
    member_name: Optional[str] = None
    total_hours: float
    total_sessions: int
    recognition_tier: Optional[RecognitionTier] = None


class VolunteerDashboardSummary(BaseModel):
    total_active_volunteers: int
    total_hours_this_month: float
    upcoming_opportunities: int
    unfilled_slots: int
    no_show_rate: float
    top_volunteers: list[LeaderboardEntry]


# ============================================================================
# SPOTLIGHT SCHEMAS
# ============================================================================


class SpotlightFeaturedVolunteer(BaseModel):
    member_id: uuid.UUID
    member_name: str
    profile_photo_url: Optional[str] = None
    spotlight_quote: Optional[str] = None
    recognition_tier: Optional[RecognitionTier] = None
    total_hours: float
    preferred_roles: Optional[list[str]] = None


class SpotlightMilestone(BaseModel):
    description: str
    count: int


class SpotlightResponse(BaseModel):
    featured_volunteer: Optional[SpotlightFeaturedVolunteer] = None
    total_active_volunteers: int
    total_hours_all_time: float
    milestones_this_month: list[SpotlightMilestone] = []
    top_volunteers: list[LeaderboardEntry] = []


class FeatureVolunteerRequest(BaseModel):
    spotlight_quote: Optional[str] = None
    featured_until: Optional[datetime] = None
