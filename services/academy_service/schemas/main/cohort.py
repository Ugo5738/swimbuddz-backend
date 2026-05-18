"""Cohort and cohort-resource schemas, including timeline-shift requests."""

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from libs.common.currency import kobo_to_naira, naira_to_kobo
from services.academy_service.models import CohortStatus, CohortType, LocationType

from .program import ProgramResponse


class CohortBase(BaseModel):
    name: str
    start_date: datetime
    end_date: datetime
    capacity: int
    status: CohortStatus = CohortStatus.OPEN
    # Cohort variant — see A1 Phase 3.2.
    # GROUP (default, 8–12), PRIVATE (1:1), SMALL_GROUP (2–6 member-specified),
    # CORPORATE (sponsor-commissioned). All such cohorts produce
    # SessionType.COHORT_CLASS sessions; the type just controls capacity /
    # billing / enrollment semantics at the academy level.
    type: CohortType = CohortType.GROUP
    # Cross-service reference to a future corporate-wellness model (sponsor
    # billing terms, enrolment ingest, etc). Plain UUID, no FK.
    corporate_program_id: Optional[UUID] = None
    allow_mid_entry: bool = False
    mid_entry_cutoff_week: int = 2  # Max week number for mid-entry
    require_approval: bool = (
        False  # If True, enrollment needs admin approval even after payment
    )
    # If True, reaching 2 missed installments moves enrollment to DROPOUT_PENDING
    # and requires admin to manually confirm the dropout instead of auto-dropping.
    admin_dropout_approval: bool = False
    # Location
    timezone: Optional[str] = None
    location_type: Optional[LocationType] = None
    location_name: Optional[str] = None
    location_address: Optional[str] = None
    # Preferred: pool_id from the pools registry.
    # When set, session generation tags every session with this pool.
    pool_id: Optional[UUID] = None
    # Pricing override
    price_override: Optional[int] = None  # API contract: naira (major unit)
    notes_internal: Optional[str] = None
    # ── Session defaults (applied to every session generated for this cohort) ──
    # API contract: default_pool_fee in naira (major unit); DB stores kobo.
    default_pool_fee: Optional[float] = None
    # Ride configs as a list of dicts. Client sends naira costs; schema converts
    # to kobo in the `cost_kobo` field so backend consumers have consistent units.
    #   [{"ride_area_id": "uuid", "cost": 5000.0, "capacity": 4}, ...]
    default_ride_configs: Optional[list[dict]] = None
    # ── Installment billing ──────────────────────────────────────────────────
    # Toggle: admin enables installment option for this cohort
    installment_plan_enabled: bool = False
    # Optional overrides — if None, business logic auto-computes from fee + duration
    installment_count: Optional[int] = None  # Override auto-computed count
    installment_deposit_amount: Optional[int] = (
        None  # API contract: override first-installment amount (₦)
    )


class CoachAssignmentInput(BaseModel):
    """Input for creating coach assignments during cohort creation."""

    coach_id: UUID
    role: str = "lead"  # "lead", "assistant"


class CohortCreate(CohortBase):
    program_id: UUID
    coach_assignments: Optional[list[CoachAssignmentInput]] = None

    @field_validator(
        "price_override",
        "installment_deposit_amount",
        "default_pool_fee",
        mode="before",
    )
    @classmethod
    def convert_amounts_to_kobo(cls, value):
        return naira_to_kobo(value) if value is not None else None

    @field_validator("default_ride_configs", mode="before")
    @classmethod
    def convert_ride_config_costs_to_kobo(cls, value):
        """Convert each ride-config entry's `cost` (naira) → `cost_kobo` (int)."""
        if value is None:
            return None
        normalised: list[dict] = []
        for entry in value:
            if not isinstance(entry, dict):
                continue
            out = dict(entry)
            if "cost" in out and out["cost"] is not None:
                out["cost_kobo"] = naira_to_kobo(out.pop("cost"))
            normalised.append(out)
        return normalised


class CohortUpdate(BaseModel):
    name: Optional[str] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    capacity: Optional[int] = None
    status: Optional[CohortStatus] = None
    type: Optional[CohortType] = None
    corporate_program_id: Optional[UUID] = None
    allow_mid_entry: Optional[bool] = None
    mid_entry_cutoff_week: Optional[int] = None
    require_approval: Optional[bool] = None
    admin_dropout_approval: Optional[bool] = None
    # Location
    timezone: Optional[str] = None
    location_type: Optional[LocationType] = None
    location_name: Optional[str] = None
    location_address: Optional[str] = None
    pool_id: Optional[UUID] = None
    # Pricing override
    price_override: Optional[int] = None
    notes_internal: Optional[str] = None
    # ── Session defaults ─────────────────────────────────────────────────
    default_pool_fee: Optional[float] = None
    default_ride_configs: Optional[list[dict]] = None
    # ── Installment billing ──────────────────────────────────────────────────
    installment_plan_enabled: Optional[bool] = None
    installment_count: Optional[int] = None
    installment_deposit_amount: Optional[int] = None

    @field_validator(
        "price_override",
        "installment_deposit_amount",
        "default_pool_fee",
        mode="before",
    )
    @classmethod
    def convert_amounts_to_kobo(cls, value):
        return naira_to_kobo(value) if value is not None else None

    @field_validator("default_ride_configs", mode="before")
    @classmethod
    def convert_ride_config_costs_to_kobo(cls, value):
        if value is None:
            return None
        normalised: list[dict] = []
        for entry in value:
            if not isinstance(entry, dict):
                continue
            out = dict(entry)
            if "cost" in out and out["cost"] is not None:
                out["cost_kobo"] = naira_to_kobo(out.pop("cost"))
            normalised.append(out)
        return normalised


class CohortTimelineShiftRequest(BaseModel):
    """Request payload for timeline-shifting a cohort and linked records."""

    new_start_date: datetime
    new_end_date: datetime
    expected_updated_at: Optional[datetime] = None
    idempotency_key: Optional[str] = None
    reason: Optional[str] = None
    shift_sessions: bool = True
    shift_installments: bool = True
    reset_start_reminders: bool = True
    notify_members: bool = True
    set_status_to_open_if_future: bool = True


class CohortTimelineSessionImpact(BaseModel):
    session_id: str
    status: str
    starts_at: datetime
    ends_at: datetime
    new_starts_at: datetime
    new_ends_at: datetime
    will_shift: bool


class CohortTimelineShiftPreviewResponse(BaseModel):
    cohort_id: UUID
    old_start_date: datetime
    old_end_date: datetime
    new_start_date: datetime
    new_end_date: datetime
    delta_seconds: int
    already_applied: bool = False
    sessions_total: int = 0
    sessions_shiftable: int = 0
    sessions_blocked: int = 0
    pending_installments: int = 0
    reminder_resets_possible: int = 0
    session_impacts: List[CohortTimelineSessionImpact] = Field(default_factory=list)


class CohortTimelineShiftApplyResponse(BaseModel):
    cohort_id: UUID
    old_start_date: datetime
    old_end_date: datetime
    new_start_date: datetime
    new_end_date: datetime
    delta_seconds: int
    already_applied: bool = False
    sessions_shifted: int = 0
    sessions_skipped: int = 0
    pending_installments_shifted: int = 0
    reminder_resets_applied: int = 0
    notification_attempts: int = 0
    notification_sent: int = 0
    warnings: List[str] = Field(default_factory=list)


class CohortTimelineShiftLogResponse(BaseModel):
    id: UUID
    cohort_id: UUID
    idempotency_key: Optional[str] = None
    actor_auth_id: Optional[str] = None
    actor_member_id: Optional[UUID] = None
    reason: Optional[str] = None
    old_start_date: datetime
    old_end_date: datetime
    new_start_date: datetime
    new_end_date: datetime
    delta_seconds: int
    options_json: Dict[str, Any] = Field(default_factory=dict)
    results_json: Dict[str, Any] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CohortResponse(CohortBase):
    id: UUID
    program_id: UUID
    coach_id: Optional[UUID] = None
    # Resolved from members-service by list endpoints (best-effort). None when
    # no coach is assigned or members-service is unavailable.
    coach_name: Optional[str] = None
    admin_dropout_approval: bool = False
    created_at: datetime
    updated_at: datetime

    # Include program details for UI convenience (avoid extra client round trips).
    program: Optional[ProgramResponse] = None

    # Populated by list endpoints (/cohorts/open, /cohorts/enrollable) so the
    # frontend can drive waitlist UX without a second round trip. None on
    # endpoints that don't compute them (e.g. detail lookups).
    enrolled_count: Optional[int] = None
    is_full: Optional[bool] = None

    @field_validator(
        "price_override",
        "installment_deposit_amount",
        "default_pool_fee",
        mode="before",
    )
    @classmethod
    def convert_amounts_to_naira(cls, value):
        return kobo_to_naira(value) if value is not None else None

    @field_validator("default_ride_configs", mode="before")
    @classmethod
    def convert_ride_config_costs_to_naira(cls, value):
        """On read: restore `cost` (naira, float) from stored `cost_kobo` (int)."""
        if value is None:
            return None
        normalised: list[dict] = []
        for entry in value:
            if not isinstance(entry, dict):
                continue
            out = dict(entry)
            if "cost_kobo" in out and out["cost_kobo"] is not None:
                out["cost"] = kobo_to_naira(out.pop("cost_kobo"))
            normalised.append(out)
        return normalised

    model_config = ConfigDict(from_attributes=True)


# --- Cohort Resource Schemas ---


class CohortResourceBase(BaseModel):
    title: str
    resource_type: str  # 'note', 'drill', 'assignment'
    content_media_id: Optional[UUID] = None
    description: Optional[str] = None


class CohortResourceCreate(CohortResourceBase):
    cohort_id: UUID


class CohortResourceResponse(CohortResourceBase):
    id: UUID
    cohort_id: UUID
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
