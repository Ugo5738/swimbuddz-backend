"""Schemas for the Club entity."""

import re
import uuid
from datetime import date, datetime, time
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from services.members_service.models.enums import DayOfWeek

# slug: lowercase, digits, single hyphens; 2-40 chars; no leading/trailing hyphen
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class ClubBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    slug: str = Field(..., min_length=2, max_length=40)
    description: Optional[str] = None
    location: Optional[str] = None
    # Default session schedule pods inherit at creation. See
    # docs/club/POD_OPERATIONS.md "Saturday session — anchored, with override".
    default_session_day: Optional[DayOfWeek] = None
    default_session_time: Optional[time] = None
    default_session_duration_minutes: Optional[int] = Field(default=None, ge=15, le=480)
    default_pool_id: Optional[uuid.UUID] = None
    operating_area_id: Optional[uuid.UUID] = None

    @field_validator("slug")
    @classmethod
    def _slug_format(cls, v: str) -> str:
        v = v.strip().lower()
        if not SLUG_RE.match(v):
            raise ValueError(
                "slug must be lowercase letters/numbers separated by hyphens"
            )
        return v


class ClubCreate(ClubBase):
    is_active: bool = True


class ClubUpdate(BaseModel):
    """All fields optional. slug accepts the same format if provided."""

    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    slug: Optional[str] = Field(default=None, min_length=2, max_length=40)
    description: Optional[str] = None
    location: Optional[str] = None
    is_active: Optional[bool] = None
    default_session_day: Optional[DayOfWeek] = None
    default_session_time: Optional[time] = None
    default_session_duration_minutes: Optional[int] = Field(default=None, ge=15, le=480)
    default_pool_id: Optional[uuid.UUID] = None
    operating_area_id: Optional[uuid.UUID] = None

    @field_validator("slug")
    @classmethod
    def _slug_format(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip().lower()
        if not SLUG_RE.match(v):
            raise ValueError(
                "slug must be lowercase letters/numbers separated by hyphens"
            )
        return v


class ClubResponse(ClubBase):
    id: uuid.UUID
    is_active: bool
    # Schedule fields are NOT optional in the response — every Club row in
    # the DB has them (server defaults), so the API always returns concrete
    # values. We override the base's Optional types here.
    default_session_day: DayOfWeek
    default_session_time: time
    default_session_duration_minutes: int
    default_pool_id: Optional[uuid.UUID] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ClubPlanCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=160)
    billing_cycle: Literal["quarterly"] = "quarterly"
    currency: str = Field(default="NGN", min_length=3, max_length=8)
    club_fee_kobo: int = Field(..., ge=0)
    community_experience_fee_kobo: int = Field(default=3_000_000, ge=0)
    community_experience_default_selected: bool = True
    community_experience_offering_id: Optional[uuid.UUID] = None
    sessions_included: int = Field(default=12, ge=1, le=52)
    period_start: date
    period_end: date
    minimum_entry_sessions: int = Field(default=5, ge=1, le=52)
    refreshments_included: bool = True
    capacity: Optional[int] = Field(default=None, ge=1)
    premium_venue_note: Optional[str] = None
    effective_from: date
    effective_to: Optional[date] = None
    is_active: bool = True

    @model_validator(mode="after")
    def valid_period(self):
        if self.effective_to and self.effective_to < self.effective_from:
            raise ValueError("effective_to must be on or after effective_from")
        if self.period_end < self.period_start:
            raise ValueError("period_end must be on or after period_start")
        if self.minimum_entry_sessions > self.sessions_included:
            raise ValueError("minimum_entry_sessions cannot exceed sessions_included")
        return self


class ClubPlanResponse(ClubPlanCreate):
    id: uuid.UUID
    club_id: uuid.UUID
    club_name: Optional[str] = None
    club_slug: Optional[str] = None
    location: Optional[str] = None
    operating_area_id: Optional[uuid.UUID] = None
    pool_id: Optional[uuid.UUID] = None
    default_pool_id: Optional[uuid.UUID] = None
    remaining_sessions: int = 0
    entry_available: bool = True
    entry_reason: Optional[str] = None
    current_price_kobo: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ClubApplicationCreate(BaseModel):
    plan_version_id: uuid.UUID
    plan_version_ids: list[uuid.UUID] = Field(default_factory=list, max_length=4)
    community_experience_selected: bool = True
    preferred_pod_id: Optional[uuid.UUID] = None
    notes: Optional[str] = Field(default=None, max_length=2000)


class ClubPreAssessmentUpsert(BaseModel):
    can_swim_25m_continuously: bool
    controlled_breathing: bool
    comfortable_in_deep_water: bool
    can_float_or_tread_30_seconds: bool
    can_stop_and_recover: bool
    last_swim_date: Optional[date] = None
    current_nonstop_distance_m: Optional[int] = Field(default=None, ge=0, le=100_000)
    injuries_or_accommodations: Optional[str] = Field(default=None, max_length=2000)
    notes: Optional[str] = Field(default=None, max_length=2000)


class ClubObservedAssessmentUpdate(BaseModel):
    outcome: Literal["club_ready", "club_ready_modified", "academy_first"]
    observed_checks: dict[str, Any] = Field(default_factory=dict)
    nonstop_distance_m: Optional[int] = Field(default=None, ge=0, le=100_000)
    deep_water_comfort: Optional[str] = Field(default=None, max_length=32)
    primary_technique_focus: Optional[str] = Field(default=None, max_length=2000)
    first_club_milestone: Optional[str] = Field(default=None, max_length=2000)
    assessor_notes: Optional[str] = Field(default=None, max_length=4000)
    send_result_email: bool = True
    approved_payment_modes: list[
        Literal["quarterly_prepaid", "transition_per_session"]
    ] = Field(default_factory=lambda: ["quarterly_prepaid"], min_length=1, max_length=2)
    transition_expires_at: Optional[date] = None

    @model_validator(mode="after")
    def valid_payment_arrangements(self):
        self.approved_payment_modes = list(dict.fromkeys(self.approved_payment_modes))
        if self.outcome == "academy_first":
            return self
        if "transition_per_session" in self.approved_payment_modes:
            if (
                self.transition_expires_at is not None
                and self.transition_expires_at < date.today()
            ):
                raise ValueError("transition_expires_at cannot be in the past")
        elif self.transition_expires_at:
            raise ValueError(
                "Transition expiry requires transition_per_session approval"
            )
        return self


class ClubAssessmentResponse(BaseModel):
    id: uuid.UUID
    application_id: uuid.UUID
    self_report: dict[str, Any]
    observed_checks: Optional[dict[str, Any]] = None
    assessor_member_id: Optional[uuid.UUID] = None
    outcome: str
    nonstop_distance_m: Optional[int] = None
    deep_water_comfort: Optional[str] = None
    primary_technique_focus: Optional[str] = None
    first_club_milestone: Optional[str] = None
    assessor_notes: Optional[str] = None
    completed_at: Optional[datetime] = None
    result_email_sent_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ClubApplicationResponse(BaseModel):
    id: uuid.UUID
    member_id: uuid.UUID
    member_name: Optional[str] = None
    member_email: Optional[str] = None
    club_id: uuid.UUID
    plan_version_id: uuid.UUID
    status: str
    community_experience_selected: bool
    preferred_pod_id: Optional[uuid.UUID] = None
    notes: Optional[str] = None
    quote_id: Optional[uuid.UUID] = None
    approved_payment_modes: list[
        Literal["quarterly_prepaid", "transition_per_session"]
    ] = Field(default_factory=list)
    transition_expires_at: Optional[date] = None
    selected_payment_mode: Optional[
        Literal["quarterly_prepaid", "transition_per_session"]
    ] = None
    plan: Optional[ClubPlanResponse] = None
    selected_plans: list[ClubPlanResponse] = Field(default_factory=list)
    assessment: Optional[ClubAssessmentResponse] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ClubPaymentContext(BaseModel):
    application_id: uuid.UUID
    member_auth_id: str
    club_id: uuid.UUID
    club_name: str
    plan_version_id: uuid.UUID
    plan_version_ids: list[uuid.UUID] = Field(default_factory=list)
    approved_payment_modes: list[
        Literal["quarterly_prepaid", "transition_per_session"]
    ] = Field(default_factory=list)
    payment_mode: Literal["quarterly_prepaid", "transition_per_session"]
    transition_expires_at: Optional[date] = None
    billing_cycle: str
    currency: str
    club_fee_kobo: int
    club_items: list[dict[str, Any]] = Field(default_factory=list)
    annual_membership_fee_kobo: int = 0
    annual_membership_months: int = 0
    community_experience_selected: bool
    community_experience_fee_kobo: int
    subtotal_kobo: int
    months: int = 3


class ActivateClubApplicationRequest(BaseModel):
    payment_reference: str = Field(..., min_length=1, max_length=128)
    starts_at: Optional[datetime] = None
    months: int = Field(default=3, ge=1, le=24)
    community_experience_selected: bool = False
    community_experience_fee_kobo: int = Field(default=0, ge=0)
    payment_mode: Literal["quarterly_prepaid", "transition_per_session"] = (
        "quarterly_prepaid"
    )


class ClubApplicationReservationRequest(BaseModel):
    payment_reference: str = Field(..., min_length=1, max_length=128)
    payment_mode: Literal["quarterly_prepaid", "transition_per_session"] = (
        "quarterly_prepaid"
    )


class ClubApplicationReservationResponse(BaseModel):
    application_id: uuid.UUID
    payment_reference: str
    status: Literal["active", "released", "consumed"]
    expires_at: datetime
    plan_version_ids: list[uuid.UUID] = Field(default_factory=list)


class CommunityExperienceOfferingCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=160)
    currency: str = Field(default="NGN", min_length=3, max_length=8)
    period_start: date
    period_end: date
    standard_member_fee_kobo: int = Field(default=5_000_000, ge=0)
    club_member_fee_kobo: int = Field(default=4_000_000, ge=0)
    club_bundle_fee_kobo: int = Field(default=3_000_000, ge=0)
    purchase_opens_at: Optional[datetime] = None
    purchase_closes_at: Optional[datetime] = None
    is_active: bool = True

    @model_validator(mode="after")
    def valid_experience(self):
        if self.period_end < self.period_start:
            raise ValueError("period_end must be on or after period_start")
        if not (
            self.club_bundle_fee_kobo
            <= self.club_member_fee_kobo
            <= self.standard_member_fee_kobo
        ):
            raise ValueError(
                "Experience prices must follow bundle <= Club later <= standard"
            )
        if (
            self.purchase_opens_at
            and self.purchase_closes_at
            and self.purchase_closes_at <= self.purchase_opens_at
        ):
            raise ValueError("purchase_closes_at must be after purchase_opens_at")
        return self


class CommunityExperienceOfferingResponse(CommunityExperienceOfferingCreate):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CommunityExperienceQuote(BaseModel):
    offering_id: uuid.UUID
    offering_name: str
    member_auth_id: str
    currency: str
    price_context: Literal["standard_member", "club_member_later"]
    amount_kobo: int
    annual_membership_fee_kobo: int = 0
    annual_membership_months: int = 0
    subtotal_kobo: int
    already_purchased: bool = False


class ActivateCommunityExperienceRequest(BaseModel):
    member_auth_id: str
    payment_reference: str = Field(..., min_length=1, max_length=128)
    amount_paid_kobo: int = Field(..., ge=0)
    price_context: Literal["standard_member", "club_member_later", "club_bundle"]
