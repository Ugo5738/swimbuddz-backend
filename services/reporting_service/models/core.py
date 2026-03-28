"""Reporting service database models."""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, Integer, String, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from services.reporting_service.models.enums import ReportStatus, enum_values


class QuarterlySnapshot(Base):
    """Tracks completion state of quarterly snapshot jobs."""

    __tablename__ = "quarterly_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    quarter: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[ReportStatus] = mapped_column(
        SAEnum(
            ReportStatus,
            name="report_status_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        default=ReportStatus.PENDING,
        nullable=False,
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    member_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("year", "quarter", name="uq_snapshot_year_quarter"),
    )

    def __repr__(self) -> str:
        return f"<QuarterlySnapshot Q{self.quarter} {self.year} ({self.status})>"


class MemberQuarterlyReport(Base):
    """Pre-computed individual member stats for a quarter."""

    __tablename__ = "member_quarterly_reports"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), index=True, nullable=False
    )
    member_auth_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    quarter: Mapped[int] = mapped_column(Integer, nullable=False)

    # Member info snapshot (so we don't need cross-service calls to display)
    member_name: Mapped[str] = mapped_column(String, nullable=False)
    member_tier: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # ── Attendance stats ──
    total_sessions_attended: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    total_sessions_available: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    attendance_rate: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    sessions_by_type: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    punctuality_rate: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    streak_longest: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    streak_current: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    favorite_day: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    favorite_location: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # ── Academy stats ──
    milestones_achieved: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    milestones_in_progress: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    programs_enrolled: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    certificates_earned: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # ── Financial stats ──
    total_spent_ngn: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    bubbles_earned: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    bubbles_spent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # ── Transport stats ──
    rides_taken: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rides_offered: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # ── Volunteer stats ──
    volunteer_hours: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    # ── Store stats ──
    orders_placed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    store_spent_ngn: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # ── Events stats ──
    events_attended: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # ── Pool time (hours) ──
    pool_hours: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    # ── First-timer flag (joined this quarter) ──
    is_first_quarter: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    member_joined_at: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # ── Comparative / percentile ──
    attendance_percentile: Mapped[float] = mapped_column(
        Float, default=0.0, nullable=False
    )  # e.g. 0.8 = top 20%

    # ── Academy detail ──
    academy_skills: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    cohorts_completed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # ── Privacy ──
    leaderboard_opt_out: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )

    # ── Shareable card ──
    card_image_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # ── Timestamps ──
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), onupdate=utc_now, nullable=True
    )

    __table_args__ = (
        UniqueConstraint("member_id", "year", "quarter", name="uq_member_year_quarter"),
    )

    def __repr__(self) -> str:
        return (
            f"<MemberQuarterlyReport {self.member_name} "
            f"Q{self.quarter} {self.year}>"
        )


class CommunityQuarterlyStats(Base):
    """Aggregated community-wide stats for a quarter."""

    __tablename__ = "community_quarterly_stats"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    quarter: Mapped[int] = mapped_column(Integer, nullable=False)

    total_active_members: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    total_sessions_held: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_attendance_records: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    average_attendance_rate: Mapped[float] = mapped_column(
        Float, default=0.0, nullable=False
    )
    total_new_members: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_milestones_achieved: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    total_certificates_issued: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    total_volunteer_hours: Mapped[float] = mapped_column(
        Float, default=0.0, nullable=False
    )
    total_rides_shared: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_revenue_ngn: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # ── Pool time ──
    total_pool_hours: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    # ── Location & session highlights ──
    most_active_location: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    busiest_session_title: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    busiest_session_attendance: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    most_popular_day: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    most_popular_time_slot: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # ── Academy ──
    total_cohorts_completed: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )

    stats_by_type: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    community_milestones: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)

    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("year", "quarter", name="uq_community_year_quarter"),
    )

    def __repr__(self) -> str:
        return f"<CommunityQuarterlyStats Q{self.quarter} {self.year}>"
