"""Pydantic schemas for the Member Swim Scorecard (ADR-026)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


ProficiencyLiteral = Literal["not_started", "learning", "confident", "mastered"]
DomainLiteral = Literal[
    "foundations",
    "freestyle",
    "backstroke",
    "breaststroke",
    "butterfly",
    "advanced_skills",
]
VerificationSourceLiteral = Literal["self_logged", "strokelab_ai", "coach_verified"]
VerificationStatusLiteral = Literal[
    "unverified", "pending_review", "verified", "rejected"
]
VerifierRoleLiteral = Literal["coach", "pod_lead", "admin"]
StrokeLiteral = Literal[
    "freestyle",
    "backstroke",
    "breaststroke",
    "butterfly",
    "medley",
    "open_water",
    "any",
]
BenchmarkTypeLiteral = Literal[
    "max_distance",
    "time_trial",
    "spl",
    "stroke_rate",
    "swolf",
    "open_water",
]
ViewerAudienceLiteral = Literal[
    "self", "coach_or_admin", "pod_lead", "pod_mate", "public_share"
]


# ── Skill Progress Schemas ────────────────────────────────────────────────


class SelfUpdateSkillRequest(BaseModel):
    proficiency: ProficiencyLiteral
    member_notes: Optional[str] = Field(default=None, max_length=1000)
    request_verification: bool = False


class VerifySkillRequest(BaseModel):
    action: Literal["verify", "reject"] = "verify"
    proficiency: Optional[ProficiencyLiteral] = None
    verifier_notes: Optional[str] = Field(default=None, max_length=1000)


class ScorecardSkillItem(BaseModel):
    skill_code: str
    domain: DomainLiteral
    title: str
    description: str
    ti_focal_point: str
    order_index: int
    is_safety_gate: bool

    proficiency: ProficiencyLiteral = "not_started"
    verification_source: VerificationSourceLiteral = "self_logged"
    verification_status: VerificationStatusLiteral = "unverified"
    verified_proficiency: Optional[ProficiencyLiteral] = None
    is_verified: bool = False
    is_safety_gate_cleared: bool = False
    auto_verified_via: Optional[str] = None

    verified_by_member_id: Optional[uuid.UUID] = None
    verified_by_name: Optional[str] = None
    verified_by_role: Optional[VerifierRoleLiteral] = None
    verified_at: Optional[datetime] = None

    member_notes: Optional[str] = None
    verifier_notes: Optional[str] = None
    updated_at: Optional[datetime] = None


class ScorecardDomainSection(BaseModel):
    code: DomainLiteral
    title: str
    short_title: str
    description: str
    order_index: int
    layer: str
    default_expanded_for_beginner: bool
    total_skills: int
    started_count: int
    confident_or_mastered_count: int
    verified_count: int
    pending_review_count: int
    stroke_unlocked: bool
    skills: List[ScorecardSkillItem]


# ── Performance & Efficiency Benchmark Schemas ────────────────────────────


class _BenchmarkFieldsBase(BaseModel):
    stroke: StrokeLiteral
    benchmark_type: BenchmarkTypeLiteral
    recorded_at: Optional[datetime] = None
    distance_m: Optional[int] = Field(default=None, gt=0, le=50000)
    duration_seconds: Optional[float] = Field(default=None, gt=0, le=86400)
    stroke_count: Optional[int] = Field(default=None, gt=0, le=500)
    stroke_rate_spm: Optional[float] = Field(default=None, gt=0, le=180)
    swolf_score: Optional[float] = Field(default=None, gt=0, le=500)
    pool_length_m: Optional[float] = Field(default=None, ge=5.0, le=100.0)
    pool_id: Optional[uuid.UUID] = None
    pool_name: Optional[str] = Field(default=None, max_length=160)
    session_id: Optional[uuid.UUID] = None
    benchmark_day_label: Optional[str] = Field(default=None, max_length=160)
    notes: Optional[str] = Field(default=None, max_length=1000)
    request_verification: bool = False


def _validate_benchmark_combination(
    *,
    benchmark_type: str,
    distance_m: Optional[int],
    duration_seconds: Optional[float],
    stroke_count: Optional[int],
    stroke_rate_spm: Optional[float],
    swolf_score: Optional[float],
    pool_length_m: Optional[float],
) -> None:
    if benchmark_type == "max_distance":
        if distance_m is None:
            raise ValueError("distance_m is required for max_distance benchmarks")
    elif benchmark_type == "time_trial":
        if distance_m is None or duration_seconds is None:
            raise ValueError(
                "Both distance_m and duration_seconds are required for time_trial benchmarks"
            )
    elif benchmark_type == "spl":
        if stroke_count is None:
            raise ValueError("stroke_count is required for SPL benchmarks")
        if pool_length_m is None:
            raise ValueError(
                "pool_length_m is required for SPL benchmarks so different pool lengths are never conflated"
            )
    elif benchmark_type == "stroke_rate":
        if stroke_rate_spm is None:
            raise ValueError("stroke_rate_spm is required for stroke_rate benchmarks")
    elif benchmark_type == "swolf":
        if pool_length_m is None:
            raise ValueError(
                "pool_length_m is required for SWOLF benchmarks so different pool lengths are never conflated"
            )
        if swolf_score is None and (duration_seconds is None or stroke_count is None):
            raise ValueError(
                "Provide either swolf_score OR both single-length duration_seconds and stroke_count"
            )
    elif benchmark_type == "open_water":
        if distance_m is None:
            raise ValueError("distance_m is required for open_water benchmarks")


class CreateBenchmarkRequest(_BenchmarkFieldsBase):
    @model_validator(mode="after")
    def validate_benchmark_fields(self) -> "CreateBenchmarkRequest":
        _validate_benchmark_combination(
            benchmark_type=self.benchmark_type,
            distance_m=self.distance_m,
            duration_seconds=self.duration_seconds,
            stroke_count=self.stroke_count,
            stroke_rate_spm=self.stroke_rate_spm,
            swolf_score=self.swolf_score,
            pool_length_m=self.pool_length_m,
        )
        return self


class CoachCreateBenchmarkRequest(CreateBenchmarkRequest):
    verifier_notes: Optional[str] = Field(default=None, max_length=1000)


class ReviewBenchmarkRequest(BaseModel):
    action: Literal["verify", "reject"]
    verifier_notes: Optional[str] = Field(default=None, max_length=1000)


class BatchBenchmarkEntry(_BenchmarkFieldsBase):
    member_id: uuid.UUID
    verifier_notes: Optional[str] = Field(default=None, max_length=1000)


class BatchBenchmarkDayRequest(BaseModel):
    benchmark_day_label: str = Field(min_length=2, max_length=160)
    recorded_at: Optional[datetime] = None
    session_id: Optional[uuid.UUID] = None
    pool_id: Optional[uuid.UUID] = None
    pool_name: Optional[str] = Field(default=None, max_length=160)
    pool_length_m: Optional[float] = Field(default=None, ge=5.0, le=100.0)
    entries: List[BatchBenchmarkEntry] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_entries_with_defaults(self) -> "BatchBenchmarkDayRequest":
        for idx, entry in enumerate(self.entries):
            eff_pool_len = (
                entry.pool_length_m
                if entry.pool_length_m is not None
                else self.pool_length_m
            )
            try:
                _validate_benchmark_combination(
                    benchmark_type=entry.benchmark_type,
                    distance_m=entry.distance_m,
                    duration_seconds=entry.duration_seconds,
                    stroke_count=entry.stroke_count,
                    stroke_rate_spm=entry.stroke_rate_spm,
                    swolf_score=entry.swolf_score,
                    pool_length_m=eff_pool_len,
                )
            except ValueError as exc:
                raise ValueError(f"Entry {idx + 1}: {exc}") from exc
        return self


class BenchmarkItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    member_id: uuid.UUID
    stroke: StrokeLiteral
    benchmark_type: BenchmarkTypeLiteral
    recorded_at: datetime

    distance_m: Optional[int] = None
    duration_seconds: Optional[float] = None
    pace_per_100m_seconds: Optional[float] = None
    stroke_count: Optional[int] = None
    stroke_rate_spm: Optional[float] = None
    swolf_score: Optional[float] = None

    pool_length_m: Optional[float] = None
    pool_id: Optional[uuid.UUID] = None
    pool_name: Optional[str] = None
    session_id: Optional[uuid.UUID] = None
    benchmark_day_label: Optional[str] = None

    verification_source: VerificationSourceLiteral
    verification_status: VerificationStatusLiteral
    verified_by_member_id: Optional[uuid.UUID] = None
    verified_by_name: Optional[str] = None
    verified_by_role: Optional[VerifierRoleLiteral] = None
    verified_at: Optional[datetime] = None

    notes: Optional[str] = None
    verifier_notes: Optional[str] = None
    created_at: datetime


class PersonalBestRecord(BaseModel):
    key: str
    label: str
    stroke: str
    benchmark_type: str
    value: float
    display_value: str
    unit: str
    pool_length_m: Optional[float] = None
    pool_name: Optional[str] = None
    recorded_at: datetime
    verification_source: VerificationSourceLiteral
    is_verified: bool
    benchmark_id: Optional[uuid.UUID] = None


class TrendPoint(BaseModel):
    recorded_at: datetime
    value: float
    display_value: str
    stroke: str
    pool_length_m: Optional[float] = None
    pool_name: Optional[str] = None
    verification_source: VerificationSourceLiteral
    label: Optional[str] = None


class BenchmarkTrendSeries(BaseModel):
    metric_key: str
    title: str
    unit: str
    higher_is_better: Optional[bool] = None
    points: List[TrendPoint] = Field(default_factory=list)


# ── Cross-Service Enrichment Summaries ────────────────────────────────────


class StrokeLabRunSummary(BaseModel):
    job_id: str
    stroke_type: str
    completed_at: datetime
    stroke_rate_spm: Optional[float] = None
    body_roll_proxy_degrees: Optional[float] = None
    breath_balance_left_ratio: Optional[float] = None
    focus_area: Optional[str] = None
    summary_text: Optional[str] = None


class StrokeLabSection(BaseModel):
    available: bool = False
    total_analyses: int = 0
    latest_stroke_rate_spm: Optional[float] = None
    latest_body_roll_degrees: Optional[float] = None
    latest_breath_balance_left_ratio: Optional[float] = None
    latest_focus_area: Optional[str] = None
    latest_summary_text: Optional[str] = None
    latest_completed_at: Optional[datetime] = None
    recent_runs: List[StrokeLabRunSummary] = Field(default_factory=list)


class AcademyMilestoneSummaryItem(BaseModel):
    milestone_name: str
    achieved_at: Optional[datetime] = None
    program_name: Optional[str] = None
    cohort_name: Optional[str] = None


class AcademyProgressSection(BaseModel):
    available: bool = False
    milestones_achieved: int = 0
    milestones_in_progress: int = 0
    certificates_earned: int = 0
    cohorts_completed: int = 0
    active_enrollments: int = 0
    recent_milestones: List[AcademyMilestoneSummaryItem] = Field(default_factory=list)


class ConsistencyCommunitySection(BaseModel):
    available: bool = False
    volunteer_available: bool = False
    total_sessions_attended: int = 0
    total_pool_hours: float = 0.0
    longest_streak_weeks: int = 0
    current_streak_weeks: int = 0
    punctuality_rate: Optional[float] = None
    volunteer_hours: float = 0.0
    volunteer_tier: Optional[str] = None
    volunteer_recognition_tier: Optional[str] = None
    rides_shared: int = 0
    events_attended: int = 0
    latest_quarter_label: Optional[str] = None


# ── Settings & Full Scorecard Payloads ────────────────────────────────────


class ScorecardSettingsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    pod_visibility_enabled: bool = True
    show_benchmarks_to_pod: bool = False
    directory_highlights_enabled: bool = False
    public_share_card_enabled: bool = False
    share_token: Optional[str] = None
    preferred_pool_length_m: Optional[float] = 25.0


class UpdateScorecardSettingsRequest(BaseModel):
    pod_visibility_enabled: Optional[bool] = None
    show_benchmarks_to_pod: Optional[bool] = None
    directory_highlights_enabled: Optional[bool] = None
    public_share_card_enabled: Optional[bool] = None
    preferred_pool_length_m: Optional[float] = Field(default=None, ge=5.0, le=100.0)
    rotate_share_token: bool = False


class ClubReadinessSnapshot(BaseModel):
    is_club_ready: bool = False
    outcome: Optional[str] = None
    nonstop_distance_m: Optional[int] = None
    deep_water_comfort: Optional[str] = None
    primary_technique_focus: Optional[str] = None
    first_club_milestone: Optional[str] = None
    completed_at: Optional[datetime] = None


class ScorecardHeadlineStats(BaseModel):
    total_skills: int
    verified_skills_count: int
    confident_or_mastered_count: int
    pending_review_count: int
    safety_gates_cleared: int
    safety_gates_total: int
    verified_strokes_unlocked: List[str] = Field(default_factory=list)
    top_distance_m: Optional[int] = None
    top_distance_stroke: Optional[str] = None
    top_distance_verified: bool = False
    best_spl: Optional[int] = None
    best_spl_pool_length_m: Optional[float] = None
    best_swolf: Optional[float] = None
    best_swolf_pool_length_m: Optional[float] = None
    latest_stroke_rate_spm: Optional[float] = None
    latest_stroke_rate_source: Optional[VerificationSourceLiteral] = None


class MemberScorecardResponse(BaseModel):
    member_id: uuid.UUID
    member_name: str
    first_name: str
    profile_photo_url: Optional[str] = None
    member_since: datetime
    active_programmes: List[str] = Field(default_factory=list)
    has_club_access: bool = False
    has_full_performance_access: bool = False
    viewer_audience: ViewerAudienceLiteral
    can_edit_self: bool = False
    can_verify: bool = False
    pod_name: Optional[str] = None
    club_name: Optional[str] = None

    club_readiness: ClubReadinessSnapshot
    headline: ScorecardHeadlineStats
    domains: List[ScorecardDomainSection]

    personal_bests: List[PersonalBestRecord] = Field(default_factory=list)
    benchmarks: List[BenchmarkItemResponse] = Field(default_factory=list)
    trends: List[BenchmarkTrendSeries] = Field(default_factory=list)
    strokelab: StrokeLabSection
    academy: AcademyProgressSection
    consistency: ConsistencyCommunitySection
    settings: Optional[ScorecardSettingsResponse] = None
    source_availability: Dict[str, bool] = Field(default_factory=dict)


class PeerScorecardHighlightResponse(BaseModel):
    """Sanitized positive-only view shown to active Pod-mates (ADR-026)."""

    member_id: uuid.UUID
    member_name: str
    first_name: str
    profile_photo_url: Optional[str] = None
    pod_visibility_enabled: bool
    is_club_ready: bool = False
    verified_strokes_unlocked: List[str] = Field(default_factory=list)
    verified_skills_count: int = 0
    total_skills: int = 0
    volunteer_recognition_tier: Optional[str] = None
    verified_benchmarks: List[PersonalBestRecord] = Field(default_factory=list)


class PublicShareScorecardResponse(BaseModel):
    """Strictly opt-in public highlight card (zero private notes or raw weaknesses)."""

    display_name: str
    first_name: str
    profile_photo_url: Optional[str] = None
    member_since: datetime
    active_programmes: List[str] = Field(default_factory=list)
    is_club_ready: bool = False
    verified_strokes_unlocked: List[str] = Field(default_factory=list)
    verified_skills_count: int = 0
    total_skills: int = 0
    safety_gates_cleared: int = 0
    safety_gates_total: int = 0
    top_verified_distance_m: Optional[int] = None
    top_verified_distance_stroke: Optional[str] = None
    best_verified_spl: Optional[int] = None
    best_verified_spl_pool_length_m: Optional[float] = None
    best_verified_swolf: Optional[float] = None
    best_verified_swolf_pool_length_m: Optional[float] = None
    total_sessions_attended: Optional[int] = None
    total_pool_hours: Optional[float] = None
    volunteer_recognition_tier: Optional[str] = None
    domain_highlights: List[Dict[str, Any]] = Field(default_factory=list)


# ── Review Queue, Roster & Batch Response Schemas ─────────────────────────


class PendingSkillReviewItem(BaseModel):
    progress_id: uuid.UUID
    member_id: uuid.UUID
    member_name: str
    pod_name: Optional[str] = None
    skill_code: str
    skill_title: str
    domain: DomainLiteral
    is_safety_gate: bool
    claimed_proficiency: ProficiencyLiteral
    verified_proficiency: Optional[ProficiencyLiteral] = None
    member_notes: Optional[str] = None
    updated_at: datetime


class PendingBenchmarkReviewItem(BaseModel):
    benchmark_id: uuid.UUID
    member_id: uuid.UUID
    member_name: str
    pod_name: Optional[str] = None
    stroke: StrokeLiteral
    benchmark_type: BenchmarkTypeLiteral
    recorded_at: datetime
    distance_m: Optional[int] = None
    duration_seconds: Optional[float] = None
    pace_per_100m_seconds: Optional[float] = None
    stroke_count: Optional[int] = None
    stroke_rate_spm: Optional[float] = None
    swolf_score: Optional[float] = None
    pool_length_m: Optional[float] = None
    pool_name: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime


class ScorecardReviewQueueResponse(BaseModel):
    actor_role: VerifierRoleLiteral
    scoped_pod_ids: List[uuid.UUID] = Field(default_factory=list)
    pending_skills: List[PendingSkillReviewItem] = Field(default_factory=list)
    pending_benchmarks: List[PendingBenchmarkReviewItem] = Field(default_factory=list)


class ScorecardRosterMemberSummary(BaseModel):
    member_id: uuid.UUID
    member_name: str
    email: Optional[str] = None
    profile_photo_url: Optional[str] = None
    has_club_access: bool = False
    is_club_ready: bool = False
    pod_id: Optional[uuid.UUID] = None
    pod_name: Optional[str] = None
    verified_skills_count: int = 0
    total_skills: int = 0
    pending_reviews_count: int = 0
    verified_strokes_unlocked: List[str] = Field(default_factory=list)
    top_distance_m: Optional[int] = None
    last_benchmark_at: Optional[datetime] = None


class PodScorecardOverviewResponse(BaseModel):
    pod_id: uuid.UUID
    pod_name: str
    viewer_can_verify: bool = False
    members: List[PeerScorecardHighlightResponse] = Field(default_factory=list)


class BatchBenchmarkDayResponse(BaseModel):
    benchmark_day_label: str
    recorded_count: int
    benchmarks: List[BenchmarkItemResponse]
