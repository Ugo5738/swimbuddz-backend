"""Member Swim Scorecard models (ADR-026).

Owned by `members_service` and keyed to `members.id` so a swimmer's skills,
benchmarks, and privacy preferences travel seamlessly across Community,
Academy, and Club without being tied to a single cohort or enrollment.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from libs.common.datetime_utils import utc_now
from libs.db.base import Base


SKILL_PROFICIENCIES = ("not_started", "learning", "confident", "mastered")
SKILL_DOMAINS = (
    "foundations",
    "freestyle",
    "backstroke",
    "breaststroke",
    "butterfly",
    "advanced_skills",
)
VERIFICATION_SOURCES = ("self_logged", "strokelab_ai", "coach_verified")
VERIFICATION_STATUSES = ("unverified", "pending_review", "verified", "rejected")
VERIFIER_ROLES = ("coach", "pod_lead", "admin")
BENCHMARK_STROKES = (
    "freestyle",
    "backstroke",
    "breaststroke",
    "butterfly",
    "medley",
    "open_water",
    "any",
)
BENCHMARK_TYPES = (
    "max_distance",
    "time_trial",
    "spl",
    "stroke_rate",
    "swolf",
    "open_water",
)


class MemberSkillProgress(Base):
    """Per-member progression state for a single canonical swim skill.

    Preserves a lossless `verified_proficiency` snapshot alongside the current
    `proficiency` so a member self-updating a skill can never erase or
    downgrade a prior Coach / Pod-Lead / Admin verification (`ADR-026`).
    """

    __tablename__ = "member_skill_progress"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("members.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    skill_code: Mapped[str] = mapped_column(String(64), nullable=False)
    domain: Mapped[str] = mapped_column(String(32), nullable=False)

    # Current declared or verified stage.
    proficiency: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="not_started",
        server_default="not_started",
    )
    verification_source: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="self_logged",
        server_default="self_logged",
    )
    verification_status: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="unverified",
        server_default="unverified",
    )

    # Lossless verified baseline — set ONLY by Coach / Pod Lead / Admin or
    # automated trusted system checks (Club Readiness / Academy milestones).
    verified_proficiency: Mapped[Optional[str]] = mapped_column(
        String(24), nullable=True
    )
    verified_by_member_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("members.id", ondelete="SET NULL"),
        nullable=True,
    )
    verified_by_role: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)
    verified_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Member reflection notes and private coach/verifier feedback.
    member_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    verifier_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    last_self_updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "member_id",
            "skill_code",
            name="uq_member_skill_progress_member_skill",
        ),
        CheckConstraint(
            "proficiency IN ('not_started', 'learning', 'confident', 'mastered')",
            name="ck_member_skill_progress_proficiency",
        ),
        CheckConstraint(
            "verified_proficiency IS NULL OR verified_proficiency IN "
            "('not_started', 'learning', 'confident', 'mastered')",
            name="ck_member_skill_progress_verified_proficiency",
        ),
        CheckConstraint(
            "domain IN ('foundations', 'freestyle', 'backstroke', "
            "'breaststroke', 'butterfly', 'advanced_skills')",
            name="ck_member_skill_progress_domain",
        ),
        CheckConstraint(
            "verification_source IN ('self_logged', 'strokelab_ai', 'coach_verified')",
            name="ck_member_skill_progress_source",
        ),
        CheckConstraint(
            "verification_status IN ('unverified', 'pending_review', 'verified', 'rejected')",
            name="ck_member_skill_progress_status",
        ),
        CheckConstraint(
            "verified_by_role IS NULL OR verified_by_role IN ('coach', 'pod_lead', 'admin')",
            name="ck_member_skill_progress_verifier_role",
        ),
        Index(
            "ix_member_skill_progress_review_queue",
            "verification_status",
            "updated_at",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<MemberSkillProgress member={self.member_id} "
            f"skill={self.skill_code} proficiency={self.proficiency}>"
        )


class MemberSwimBenchmark(Base):
    """Pool-length-aware performance and efficiency benchmark entry.

    Covers max continuous distance, timed distance trials, Strokes Per Length
    (SPL), Stroke Rate (SPM), SWOLF, and Open Water swims across the 3-tier
    trust model (`ADR-026`).
    """

    __tablename__ = "member_swim_benchmarks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("members.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    stroke: Mapped[str] = mapped_column(String(24), nullable=False)
    benchmark_type: Mapped[str] = mapped_column(String(24), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    # Core quantitative measurements (all nullable depending on benchmark_type).
    distance_m: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    duration_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    pace_per_100m_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    stroke_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    stroke_rate_spm: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    swolf_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Venue & pool-length context (cross-service ref to pools_service.pools.id; no DB FK).
    pool_length_m: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    pool_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    pool_name: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)

    # Optional Benchmark Day / session provenance (cross-service ref to sessions_service).
    session_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    benchmark_day_label: Mapped[Optional[str]] = mapped_column(
        String(160), nullable=True
    )

    # 3-tier trust & audit attribution.
    verification_source: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="self_logged",
        server_default="self_logged",
    )
    verification_status: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="unverified",
        server_default="unverified",
    )
    verified_by_member_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("members.id", ondelete="SET NULL"),
        nullable=True,
    )
    verified_by_role: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)
    verified_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    verifier_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "stroke IN ('freestyle', 'backstroke', 'breaststroke', "
            "'butterfly', 'medley', 'open_water', 'any')",
            name="ck_member_swim_benchmarks_stroke",
        ),
        CheckConstraint(
            "benchmark_type IN ('max_distance', 'time_trial', 'spl', "
            "'stroke_rate', 'swolf', 'open_water')",
            name="ck_member_swim_benchmarks_type",
        ),
        CheckConstraint(
            "verification_source IN ('self_logged', 'strokelab_ai', 'coach_verified')",
            name="ck_member_swim_benchmarks_source",
        ),
        CheckConstraint(
            "verification_status IN ('unverified', 'pending_review', 'verified', 'rejected')",
            name="ck_member_swim_benchmarks_status",
        ),
        CheckConstraint(
            "verified_by_role IS NULL OR verified_by_role IN ('coach', 'pod_lead', 'admin')",
            name="ck_member_swim_benchmarks_verifier_role",
        ),
        CheckConstraint(
            "distance_m IS NULL OR distance_m > 0",
            name="ck_member_swim_benchmarks_distance_pos",
        ),
        CheckConstraint(
            "duration_seconds IS NULL OR duration_seconds > 0",
            name="ck_member_swim_benchmarks_duration_pos",
        ),
        CheckConstraint(
            "stroke_count IS NULL OR stroke_count > 0",
            name="ck_member_swim_benchmarks_stroke_count_pos",
        ),
        CheckConstraint(
            "stroke_rate_spm IS NULL OR (stroke_rate_spm > 0 AND stroke_rate_spm <= 180)",
            name="ck_member_swim_benchmarks_spm_range",
        ),
        CheckConstraint(
            "swolf_score IS NULL OR swolf_score > 0",
            name="ck_member_swim_benchmarks_swolf_pos",
        ),
        CheckConstraint(
            "pool_length_m IS NULL OR (pool_length_m >= 5 AND pool_length_m <= 100)",
            name="ck_member_swim_benchmarks_pool_length_range",
        ),
        Index(
            "ix_member_swim_benchmarks_member_recorded",
            "member_id",
            "recorded_at",
        ),
        Index(
            "ix_member_swim_benchmarks_member_type_stroke",
            "member_id",
            "benchmark_type",
            "stroke",
        ),
        Index(
            "ix_member_swim_benchmarks_review_queue",
            "verification_status",
            "created_at",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<MemberSwimBenchmark member={self.member_id} "
            f"type={self.benchmark_type} stroke={self.stroke}>"
        )


class MemberScorecardSettings(Base):
    """Per-member audience-scoped privacy and sharing settings (`ADR-026`)."""

    __tablename__ = "member_scorecard_settings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("members.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    # Pod peer visibility: positive verified highlights only (default ON for Pod cohesion).
    pod_visibility_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    # Opt-in to also show verified distance/pace PBs to active Pod-mates for lane matching.
    show_benchmarks_to_pod: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    # Opt-in to surface verified stroke badges on the community member directory.
    directory_highlights_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    # Strictly opt-in public shareable Swim Scorecard card link.
    public_share_card_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    share_token: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, unique=True, index=True
    )
    # Convenience default pool length for logging SPL/SWOLF/time trials.
    preferred_pool_length_m: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True, default=25.0, server_default="25.0"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "preferred_pool_length_m IS NULL OR "
            "(preferred_pool_length_m >= 5 AND preferred_pool_length_m <= 100)",
            name="ck_member_scorecard_settings_pool_length_range",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<MemberScorecardSettings member={self.member_id} "
            f"pod={self.pod_visibility_enabled} public={self.public_share_card_enabled}>"
        )
