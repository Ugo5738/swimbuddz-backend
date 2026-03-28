"""Pydantic response models for reporting endpoints."""

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class MemberQuarterlyReportResponse(BaseModel):
    """Individual member's quarterly report."""

    id: uuid.UUID
    member_id: uuid.UUID
    year: int
    quarter: int
    member_name: str
    member_tier: Optional[str] = None

    # Attendance
    total_sessions_attended: int
    total_sessions_available: int
    attendance_rate: float
    sessions_by_type: Optional[dict] = None
    punctuality_rate: float
    streak_longest: int
    streak_current: int
    favorite_day: Optional[str] = None
    favorite_location: Optional[str] = None

    # Academy
    milestones_achieved: int
    milestones_in_progress: int
    programs_enrolled: int
    certificates_earned: int

    # Financial
    total_spent_ngn: int
    bubbles_earned: int
    bubbles_spent: int

    # Transport
    rides_taken: int
    rides_offered: int

    # Volunteer
    volunteer_hours: float

    # Store
    orders_placed: int
    store_spent_ngn: int

    # Events
    events_attended: int

    # Pool time
    pool_hours: float = 0.0

    # First-timer
    is_first_quarter: bool = False
    member_joined_at: Optional[str] = None

    # Percentile
    attendance_percentile: float = 0.0

    # Academy detail
    academy_skills: Optional[list] = None
    cohorts_completed: int = 0

    # Privacy
    leaderboard_opt_out: bool

    # Card
    card_image_path: Optional[str] = None

    # Timestamps
    computed_at: datetime

    model_config = {"from_attributes": True}


class QuarterlyReportSummary(BaseModel):
    """Lightweight summary for the quarter selector."""

    year: int
    quarter: int
    label: str
    status: str
    computed_at: Optional[datetime] = None


class PrivacyToggleRequest(BaseModel):
    """Toggle leaderboard opt-out for a quarter."""

    year: int
    quarter: int
    leaderboard_opt_out: bool


class CommunityQuarterlyStatsResponse(BaseModel):
    """Community-wide quarterly stats."""

    year: int
    quarter: int
    total_active_members: int
    total_sessions_held: int
    total_attendance_records: int
    average_attendance_rate: float
    total_new_members: int
    total_milestones_achieved: int
    total_certificates_issued: int
    total_volunteer_hours: float
    total_rides_shared: int
    total_revenue_ngn: int
    total_pool_hours: float = 0.0
    most_active_location: Optional[str] = None
    busiest_session_title: Optional[str] = None
    busiest_session_attendance: int = 0
    most_popular_day: Optional[str] = None
    most_popular_time_slot: Optional[str] = None
    total_cohorts_completed: int = 0
    stats_by_type: Optional[dict] = None
    community_milestones: Optional[list] = None
    computed_at: datetime

    model_config = {"from_attributes": True}


class LeaderboardEntry(BaseModel):
    """Single entry in a leaderboard."""

    rank: int
    member_id: uuid.UUID
    member_name: str
    value: float
    is_current_user: bool = False


class LeaderboardResponse(BaseModel):
    """Leaderboard for a category."""

    category: str
    year: int
    quarter: int
    entries: list[LeaderboardEntry]


class GenerateReportRequest(BaseModel):
    """Admin request to trigger report generation."""

    year: int
    quarter: int


class SnapshotStatusResponse(BaseModel):
    """Status of a quarterly snapshot job."""

    year: int
    quarter: int
    status: str
    member_count: int
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None

    model_config = {"from_attributes": True}
