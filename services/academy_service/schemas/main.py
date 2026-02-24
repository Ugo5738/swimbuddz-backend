from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator
from services.academy_service.models import (
    BillingType,
    CoachGrade,
    CohortStatus,
    EnrollmentStatus,
    InstallmentStatus,
    LocationType,
    MilestoneType,
    PaymentStatus,
    ProgramCategory,
    ProgramLevel,
    ProgressStatus,
    RequiredEvidence,
)

KOBO_PER_NAIRA = 100


def _naira_to_kobo(value: int | None) -> int | None:
    if value is None:
        return None
    return int(value) * KOBO_PER_NAIRA


def _kobo_to_naira(value: int | None) -> int | None:
    if value is None:
        return None
    return int(value) // KOBO_PER_NAIRA


# --- Program Schemas ---


class ProgramBase(BaseModel):
    name: str
    description: Optional[str] = None
    cover_image_media_id: Optional[UUID] = None
    level: ProgramLevel
    duration_weeks: int
    default_capacity: int = 10
    # Pricing
    currency: str = "NGN"
    price_amount: int = 0  # API contract: naira (major unit)
    billing_type: BillingType = BillingType.ONE_TIME
    # Content
    curriculum_json: Optional[Dict[str, Any]] = None
    prep_materials: Optional[Dict[str, Any]] = None
    # Status
    is_published: bool = False


class ProgramCreate(ProgramBase):
    @field_validator("price_amount", mode="before")
    @classmethod
    def convert_price_amount_to_kobo(cls, value: int) -> int:
        return _naira_to_kobo(value) or 0


class ProgramUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    cover_image_media_id: Optional[UUID] = None
    level: Optional[ProgramLevel] = None
    duration_weeks: Optional[int] = None
    default_capacity: Optional[int] = None
    currency: Optional[str] = None
    price_amount: Optional[int] = None
    billing_type: Optional[BillingType] = None
    curriculum_json: Optional[Dict[str, Any]] = None
    prep_materials: Optional[Dict[str, Any]] = None
    is_published: Optional[bool] = None

    @field_validator("price_amount", mode="before")
    @classmethod
    def convert_price_amount_to_kobo(cls, value: Optional[int]) -> Optional[int]:
        return _naira_to_kobo(value)


class ProgramResponse(ProgramBase):
    id: UUID
    version: int = 1
    cover_image_url: Optional[str] = None  # Resolved from media_id
    created_at: datetime
    updated_at: datetime

    @field_validator("price_amount", mode="before")
    @classmethod
    def convert_price_amount_to_naira(cls, value: int) -> int:
        return _kobo_to_naira(value) or 0

    model_config = ConfigDict(from_attributes=True)


# --- Milestone Schemas ---


class MilestoneBase(BaseModel):
    name: str
    criteria: Optional[str] = None
    video_media_id: Optional[UUID] = None
    # Organization & Type
    order_index: int = 0
    milestone_type: MilestoneType = MilestoneType.SKILL
    # Assessment
    required_evidence: RequiredEvidence = RequiredEvidence.NONE
    rubric_json: Optional[Dict[str, Any]] = None


class MilestoneCreate(MilestoneBase):
    program_id: UUID


class MilestoneUpdate(BaseModel):
    name: Optional[str] = None
    criteria: Optional[str] = None
    video_media_id: Optional[UUID] = None
    order_index: Optional[int] = None
    milestone_type: Optional[MilestoneType] = None
    required_evidence: Optional[RequiredEvidence] = None
    rubric_json: Optional[Dict[str, Any]] = None


class MilestoneResponse(MilestoneBase):
    id: UUID
    program_id: UUID
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# --- Cohort Schemas ---


class CohortBase(BaseModel):
    name: str
    start_date: datetime
    end_date: datetime
    capacity: int
    status: CohortStatus = CohortStatus.OPEN
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
    # Pricing override
    price_override: Optional[int] = None  # API contract: naira (major unit)
    notes_internal: Optional[str] = None
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
    coach_id: Optional[UUID] = None  # Legacy field, still supported
    coach_assignments: Optional[list[CoachAssignmentInput]] = None

    @field_validator("price_override", "installment_deposit_amount", mode="before")
    @classmethod
    def convert_amounts_to_kobo(cls, value: Optional[int]) -> Optional[int]:
        return _naira_to_kobo(value)


class CohortUpdate(BaseModel):
    name: Optional[str] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    capacity: Optional[int] = None
    status: Optional[CohortStatus] = None
    coach_id: Optional[UUID] = None
    allow_mid_entry: Optional[bool] = None
    mid_entry_cutoff_week: Optional[int] = None
    require_approval: Optional[bool] = None
    admin_dropout_approval: Optional[bool] = None
    # Location
    timezone: Optional[str] = None
    location_type: Optional[LocationType] = None
    location_name: Optional[str] = None
    location_address: Optional[str] = None
    # Pricing override
    price_override: Optional[int] = None
    notes_internal: Optional[str] = None
    # ── Installment billing ──────────────────────────────────────────────────
    installment_plan_enabled: Optional[bool] = None
    installment_count: Optional[int] = None
    installment_deposit_amount: Optional[int] = None

    @field_validator("price_override", "installment_deposit_amount", mode="before")
    @classmethod
    def convert_amounts_to_kobo(cls, value: Optional[int]) -> Optional[int]:
        return _naira_to_kobo(value)


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
    admin_dropout_approval: bool = False
    created_at: datetime
    updated_at: datetime

    # Include program details for UI convenience (avoid extra client round trips).
    program: Optional[ProgramResponse] = None

    @field_validator("price_override", "installment_deposit_amount", mode="before")
    @classmethod
    def convert_amounts_to_naira(cls, value: Optional[int]) -> Optional[int]:
        return _kobo_to_naira(value)

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


# --- Enrollment Schemas ---


class EnrollmentBase(BaseModel):
    status: EnrollmentStatus = EnrollmentStatus.ENROLLED
    payment_status: PaymentStatus = PaymentStatus.PENDING


class EnrollmentCreate(BaseModel):
    program_id: UUID
    cohort_id: Optional[UUID] = None
    member_id: UUID
    preferences: Optional[Dict[str, Any]] = None


class EnrollmentUpdate(BaseModel):
    status: Optional[EnrollmentStatus] = None
    payment_status: Optional[PaymentStatus] = None
    cohort_id: Optional[UUID] = None  # Allow assigning/changing cohort
    access_suspended: Optional[bool] = None
    missed_installments_count: Optional[int] = None


class EnrollmentInstallmentResponse(BaseModel):
    id: UUID
    installment_number: int
    amount: int  # Kobo (minor NGN unit)
    due_at: datetime
    status: InstallmentStatus
    paid_at: Optional[datetime] = None
    payment_reference: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class EnrollmentMarkPaidRequest(BaseModel):
    installment_id: Optional[UUID] = None
    installment_number: Optional[int] = Field(default=None, ge=1)
    clear_installments: bool = False
    payment_reference: Optional[str] = None
    paid_at: Optional[datetime] = None


class AdminDropoutActionRequest(BaseModel):
    """Admin action on an enrollment that is in DROPOUT_PENDING state."""

    action: str  # "approve" → confirm drop, "reverse" → reinstate to ENROLLED
    note: Optional[str] = None  # Optional admin note for the record


class EnrollmentResponse(EnrollmentBase):
    id: UUID
    program_id: Optional[UUID] = (
        None  # Optional for backward compat if needed, but model has it nullable=True
    )
    cohort_id: Optional[UUID] = None
    member_id: UUID
    preferences: Optional[Dict[str, Any]] = None
    created_at: datetime
    updated_at: datetime
    total_installments: int = 0
    paid_installments_count: int = 0
    missed_installments_count: int = 0
    access_suspended: bool = False
    uses_installments: bool = False

    # Include details for UI
    cohort: Optional[CohortResponse] = None
    program: Optional[ProgramResponse] = None
    installments: List[EnrollmentInstallmentResponse] = Field(default_factory=list)

    # Member info (populated by endpoint, not from ORM)
    member_name: Optional[str] = None
    member_email: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


# --- Student Progress Schemas ---


class StudentProgressBase(BaseModel):
    status: ProgressStatus = ProgressStatus.PENDING
    coach_notes: Optional[str] = None


class StudentProgressUpdate(BaseModel):
    """Admin/Coach update - can set status, achievement time, and notes."""

    status: ProgressStatus
    achieved_at: Optional[datetime] = None
    coach_notes: Optional[str] = None
    reviewed_by_coach_id: Optional[UUID] = None
    reviewed_at: Optional[datetime] = None


class MemberMilestoneClaimRequest(BaseModel):
    """Member self-claim for a milestone - includes optional evidence via media service."""

    evidence_media_id: Optional[UUID] = (
        None  # Links to uploaded file or external URL in media service
    )
    student_notes: Optional[str] = None


class StudentProgressResponse(StudentProgressBase):
    id: UUID
    enrollment_id: UUID
    milestone_id: UUID
    achieved_at: Optional[datetime] = None
    evidence_media_id: Optional[UUID] = None
    student_notes: Optional[str] = None
    score: Optional[int] = None
    reviewed_by_coach_id: Optional[UUID] = None
    reviewed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# Resolve forward reference
StudentProgressResponse.model_rebuild()


# --- Onboarding Schema ---


class NextSessionInfo(BaseModel):
    """Information about the next scheduled session."""

    date: Optional[datetime] = None
    location: Optional[str] = None
    notes: Optional[str] = None


class OnboardingResponse(BaseModel):
    """Structured onboarding information for a new enrollment."""

    enrollment_id: UUID
    program_name: str
    cohort_name: str
    start_date: datetime
    end_date: datetime
    location: Optional[str] = None
    next_session: Optional[NextSessionInfo] = None
    prep_materials: Optional[Dict[str, Any]] = None
    dashboard_link: str
    resources_link: str
    sessions_link: str
    coach_name: Optional[str] = None
    total_milestones: int


# --- Cohort Complexity Scoring Schemas ---


class DimensionScore(BaseModel):
    """Individual dimension score with optional rationale."""

    score: int
    rationale: Optional[str] = None

    @field_validator("score")
    @classmethod
    def validate_score(cls, v: int) -> int:
        if v < 1 or v > 5:
            raise ValueError("Score must be between 1 and 5")
        return v


class CohortComplexityScoreCreate(BaseModel):
    """Create a complexity score for a cohort."""

    category: ProgramCategory
    dimension_1: DimensionScore
    dimension_2: DimensionScore
    dimension_3: DimensionScore
    dimension_4: DimensionScore
    dimension_5: DimensionScore
    dimension_6: DimensionScore
    dimension_7: DimensionScore


class CohortComplexityScoreUpdate(BaseModel):
    """Update a complexity score for a cohort."""

    category: Optional[ProgramCategory] = None
    dimension_1: Optional[DimensionScore] = None
    dimension_2: Optional[DimensionScore] = None
    dimension_3: Optional[DimensionScore] = None
    dimension_4: Optional[DimensionScore] = None
    dimension_5: Optional[DimensionScore] = None
    dimension_6: Optional[DimensionScore] = None
    dimension_7: Optional[DimensionScore] = None


class CohortComplexityScoreResponse(BaseModel):
    """Response schema for cohort complexity score."""

    id: UUID
    cohort_id: UUID
    category: ProgramCategory

    # Dimension scores
    dimension_1_score: int
    dimension_1_rationale: Optional[str] = None
    dimension_2_score: int
    dimension_2_rationale: Optional[str] = None
    dimension_3_score: int
    dimension_3_rationale: Optional[str] = None
    dimension_4_score: int
    dimension_4_rationale: Optional[str] = None
    dimension_5_score: int
    dimension_5_rationale: Optional[str] = None
    dimension_6_score: int
    dimension_6_rationale: Optional[str] = None
    dimension_7_score: int
    dimension_7_rationale: Optional[str] = None

    # Calculated fields
    total_score: int
    required_coach_grade: CoachGrade
    pay_band_min: int  # Percentage as integer (e.g., 45 = 45%)
    pay_band_max: int

    # Audit
    scored_by_id: UUID
    scored_at: datetime
    reviewed_by_id: Optional[UUID] = None
    reviewed_at: Optional[datetime] = None

    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ComplexityScoreCalculateRequest(BaseModel):
    """Request body for previewing a complexity score calculation."""

    category: ProgramCategory
    dimension_scores: List[int] = Field(..., min_length=7, max_length=7)


class ComplexityScoreCalculation(BaseModel):
    """Preview of complexity score calculation without saving."""

    total_score: int
    required_coach_grade: CoachGrade
    pay_band_min: int
    pay_band_max: int


class DimensionLabelsResponse(BaseModel):
    """Dimension labels for a given program category (UI contract)."""

    category: ProgramCategory
    labels: List[str]


class EligibleCoachResponse(BaseModel):
    """Coach eligible for a cohort based on grade requirements."""

    member_id: UUID
    name: str
    email: Optional[str] = None
    grade: CoachGrade
    total_coaching_hours: Optional[int] = None
    average_feedback_rating: Optional[float] = None


# ============================================================================
# AI SCORING SCHEMAS
# ============================================================================


class AIScoringRequest(BaseModel):
    """Request body for AI-assisted cohort complexity scoring.

    All fields are optional — if omitted, the backend will try to derive
    them from the cohort / program data.
    """

    category: Optional[ProgramCategory] = None
    age_group: Optional[str] = None
    skill_level: Optional[str] = None
    special_needs: Optional[str] = None
    location_type: Optional[str] = None
    duration_weeks: Optional[int] = None
    class_size: Optional[int] = None


class AIDimensionSuggestion(BaseModel):
    """A single AI-suggested dimension score."""

    dimension: str
    label: str
    score: int = Field(ge=1, le=5)
    rationale: str
    confidence: float = Field(ge=0, le=1)


class AIScoringResponse(BaseModel):
    """AI-suggested complexity scores for a cohort."""

    dimensions: List[AIDimensionSuggestion]
    total_score: int
    required_coach_grade: CoachGrade
    pay_band_min: int
    pay_band_max: int
    overall_rationale: str
    confidence: float
    model_used: str
    ai_request_id: Optional[str] = None


class AICoachSuggestion(BaseModel):
    """A single AI-recommended coach for a cohort."""

    member_id: UUID
    name: str
    email: Optional[str] = None
    grade: CoachGrade
    total_coaching_hours: Optional[int] = None
    average_feedback_rating: Optional[float] = None
    match_score: float = Field(ge=0, le=1, description="0-1 suitability score")
    rationale: str


class AICoachSuggestionResponse(BaseModel):
    """AI-suggested coaches ranked by suitability."""

    suggestions: List[AICoachSuggestion]
    required_coach_grade: CoachGrade
    category: ProgramCategory
    model_used: str
    ai_request_id: Optional[str] = None


# --- Cohort Schema Updates ---


class CohortWithScoreResponse(CohortResponse):
    """Cohort response including complexity score if available."""

    required_coach_grade: Optional[CoachGrade] = None
    complexity_score: Optional[CohortComplexityScoreResponse] = None


# ============================================================================
# COACH DASHBOARD SCHEMAS
# ============================================================================


class CoachDashboardSummary(BaseModel):
    """Summary data for coach dashboard home page."""

    # Cohort counts
    active_cohorts: int = 0
    upcoming_cohorts: int = 0
    completed_cohorts: int = 0

    # Student counts
    total_students: int = 0
    students_pending_approval: int = 0

    # Milestone review queue
    pending_milestone_reviews: int = 0

    # Upcoming sessions (next 7 days)
    upcoming_sessions_count: int = 0
    next_session: Optional["UpcomingSessionSummary"] = None

    # Earnings summary
    current_period_earnings: int = 0
    pending_payout: int = 0


class UpcomingSessionSummary(BaseModel):
    """Summary of an upcoming session for dashboard."""

    cohort_id: UUID
    cohort_name: str
    program_name: Optional[str] = None
    session_date: datetime
    location_name: Optional[str] = None
    enrolled_count: int = 0


class CoachCohortDetail(BaseModel):
    """Detailed cohort view for coach dashboard."""

    id: UUID
    name: str
    program_id: UUID
    program_name: str
    program_level: Optional[str] = None

    status: CohortStatus
    start_date: datetime
    end_date: datetime

    capacity: int
    enrolled_count: int
    waitlist_count: int

    location_name: Optional[str] = None
    location_address: Optional[str] = None

    # Coach-specific info
    required_grade: Optional[CoachGrade] = None
    pay_band_min: Optional[int] = None
    pay_band_max: Optional[int] = None

    # Progress tracking
    weeks_completed: int = 0
    total_weeks: int = 0
    milestones_count: int = 0
    milestones_achieved_count: int = 0


class PendingMilestoneReview(BaseModel):
    """Milestone claim waiting for coach review."""

    progress_id: UUID
    enrollment_id: UUID
    milestone_id: UUID
    milestone_name: str
    milestone_type: str

    student_member_id: UUID
    student_name: str
    student_email: Optional[str] = None

    cohort_id: UUID
    cohort_name: str

    evidence_media_id: Optional[UUID] = None
    student_notes: Optional[str] = None
    claimed_at: datetime


class MilestoneReviewAction(BaseModel):
    """Coach action on a milestone review."""

    action: str  # "approve" or "reject"
    score: Optional[int] = Field(None, ge=0, le=100)
    coach_notes: Optional[str] = None


# Update forward reference
CoachDashboardSummary.model_rebuild()
