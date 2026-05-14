"""Coach-dashboard schemas (summary card, upcoming sessions, milestone queue)."""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field

from services.academy_service.models import CoachGrade, CohortStatus


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
