"""Pydantic response schemas for flywheel metrics endpoints."""

from datetime import date, datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CohortFillSnapshotResponse(BaseModel):
    """Per-cohort fill state snapshot."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    cohort_id: UUID
    cohort_name: str
    program_name: Optional[str] = None
    capacity: int
    active_enrollments: int
    pending_approvals: int
    waitlist_count: int
    fill_rate: float
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    cohort_status: str
    days_until_start: Optional[int] = None
    snapshot_taken_at: datetime


class FunnelConversionSnapshotResponse(BaseModel):
    """Cross-service funnel conversion snapshot."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    funnel_stage: str
    cohort_period: str
    period_start: date
    period_end: date
    observation_window_days: int
    source_count: int
    converted_count: int
    conversion_rate: float
    breakdown_by_source: Optional[dict] = None
    snapshot_taken_at: datetime


class WalletEcosystemSnapshotResponse(BaseModel):
    """Wallet cross-service ecosystem snapshot."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    period_start: date
    period_end: date
    period_days: int
    active_wallet_users: int
    single_service_users: int
    cross_service_users: int
    cross_service_rate: float
    total_bubbles_spent: int
    total_topup_bubbles: int
    spend_distribution: Optional[dict] = None
    snapshot_taken_at: datetime


class FlywheelOverviewResponse(BaseModel):
    """Combined overview of all flywheel metrics — single dashboard call."""

    cohort_fill_avg: Optional[float] = Field(
        None,
        description="Average fill rate across OPEN/ACTIVE cohorts (0.0-1.0)",
    )
    open_cohorts_count: int = 0
    open_cohorts_at_risk_count: int = Field(
        0, description="Cohorts at <50% fill within 4 weeks of start"
    )

    community_to_club_rate: Optional[float] = None
    community_to_club_period: Optional[str] = None

    club_to_academy_rate: Optional[float] = None
    club_to_academy_period: Optional[str] = None

    wallet_cross_service_rate: Optional[float] = None
    wallet_active_users: int = 0

    last_refreshed_at: Optional[datetime] = None
    is_stale: bool = Field(False, description="True if no snapshot in last 36 hours")


class RefreshFlywheelResponse(BaseModel):
    """Response for manual refresh trigger."""

    job_enqueued: bool
    message: str
