import uuid
from datetime import datetime
from typing import Optional

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from services.academy_service.models.enums import (
    MilestoneType,
    ProgressStatus,
    RequiredEvidence,
    enum_values,
)
from sqlalchemy import JSON, Boolean, Date, DateTime
from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

# ============================================================================
# MILESTONE & PROGRESS MODELS
# ============================================================================


class Milestone(Base):
    __tablename__ = "milestones"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    program_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("programs.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    criteria: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    video_media_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )  # FK to media_service.media_items

    # Organization & Type
    order_index: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    milestone_type: Mapped[MilestoneType] = mapped_column(
        SAEnum(
            MilestoneType,
            name="milestone_type_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        default=MilestoneType.SKILL,
        server_default="skill",
    )

    # Assessment
    rubric_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    required_evidence: Mapped[RequiredEvidence] = mapped_column(
        SAEnum(
            RequiredEvidence,
            name="required_evidence_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        default=RequiredEvidence.NONE,
        server_default="none",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    # Relationships
    program = relationship("Program", back_populates="milestones")

    def __repr__(self):
        return f"<Milestone {self.name}>"


class ProgramInterest(Base):
    """Track members interested in being notified about program cohorts."""

    __tablename__ = "program_interests"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    program_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("programs.id"), nullable=False, index=True
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    member_auth_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    email: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # Track if we've notified them about a new cohort
    notified_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )

    # Relationships
    program = relationship("Program")

    def __repr__(self):
        return f"<ProgramInterest Program={self.program_id} Member={self.member_id}>"


class StudentProgress(Base):
    __tablename__ = "student_progress"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    enrollment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("enrollments.id", ondelete="CASCADE"),
        nullable=False,
    )
    milestone_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("milestones.id"), nullable=False
    )

    status: Mapped[ProgressStatus] = mapped_column(
        SAEnum(
            ProgressStatus,
            name="progress_status_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        default=ProgressStatus.PENDING,
    )
    achieved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Evidence & Scoring
    # Links to media_service.media_items - can be uploaded file OR external URL
    evidence_media_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Review tracking
    reviewed_by_coach_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Notes
    student_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    coach_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    # Relationships
    enrollment = relationship("Enrollment", back_populates="progress_records")
    milestone = relationship("Milestone")

    def __repr__(self):
        return f"<StudentProgress Enrollment={self.enrollment_id} Milestone={self.milestone_id}>"


# ============================================================================
# COHORT COMPLEXITY SCORING MODELS
# ============================================================================


class CohortComplexityScore(Base):
    """
    Stores complexity scoring for a cohort to determine required coach grade
    and compensation band. Based on the SwimBuddz Coach Operations Framework.

    Each program category has 7 dimensions scored 1-5 each (total 7-35).
    Score ranges:
    - 7-14: Grade 1 (Foundational)
    - 15-24: Grade 2 (Technical)
    - 25-35: Grade 3 (Advanced/Specialist)
    """

    __tablename__ = "cohort_complexity_scores"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    cohort_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cohorts.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )

    # Program category determines which dimensions are used
    # Values: "learn_to_swim", "special_populations", "institutional", etc.
    category: Mapped[str] = mapped_column(String, nullable=False)

    # Dimension scores (1-5 each) - meaning varies by category
    # See COHORT_SCORING_TOOL.md for dimension definitions per category
    dimension_1_score: Mapped[int] = mapped_column(Integer, nullable=False)
    dimension_1_rationale: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    dimension_2_score: Mapped[int] = mapped_column(Integer, nullable=False)
    dimension_2_rationale: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    dimension_3_score: Mapped[int] = mapped_column(Integer, nullable=False)
    dimension_3_rationale: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    dimension_4_score: Mapped[int] = mapped_column(Integer, nullable=False)
    dimension_4_rationale: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    dimension_5_score: Mapped[int] = mapped_column(Integer, nullable=False)
    dimension_5_rationale: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    dimension_6_score: Mapped[int] = mapped_column(Integer, nullable=False)
    dimension_6_rationale: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    dimension_7_score: Mapped[int] = mapped_column(Integer, nullable=False)
    dimension_7_rationale: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Calculated fields
    total_score: Mapped[int] = mapped_column(Integer, nullable=False)
    # Values: "grade_1", "grade_2", "grade_3"
    required_coach_grade: Mapped[str] = mapped_column(String, nullable=False)

    # Pay band (percentage as decimal, e.g., 0.45 = 45%)
    pay_band_min: Mapped[float] = mapped_column(
        Integer, nullable=False
    )  # Stored as percentage integer
    pay_band_max: Mapped[float] = mapped_column(
        Integer, nullable=False
    )  # Stored as percentage integer

    # Audit trail
    scored_by_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    scored_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    reviewed_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    # Relationships
    cohort = relationship("Cohort", back_populates="complexity_score")

    def __repr__(self):
        return f"<CohortComplexityScore Cohort={self.cohort_id} Score={self.total_score} Grade={self.required_coach_grade}>"


# ============================================================================
# COACH ASSIGNMENT MODELS
# ============================================================================


class CoachAssignment(Base):
    """Flexible coach-to-cohort assignment supporting multiple roles.

    Replaces the flat Cohort.coach_id with a many-to-many model that
    supports lead, assistant, shadow, and observer roles.
    Session-level overrides are flagged via is_session_override.
    """

    __tablename__ = "coach_assignments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    cohort_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cohorts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    coach_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )  # References Member with CoachProfile

    role: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # "lead", "assistant", "shadow", "observer"

    start_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    end_date: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    assigned_by_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active"
    )  # "active", "completed", "cancelled"

    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Session-level override (one-off replacement for a specific session)
    is_session_override: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    session_date: Mapped[Optional[datetime]] = mapped_column(
        Date, nullable=True
    )  # Only for session overrides

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    # Relationships
    cohort = relationship("Cohort", backref="coach_assignments")
    evaluations = relationship(
        "ShadowEvaluation",
        back_populates="assignment",
        cascade="all, delete-orphan",
    )

    def __repr__(self):
        return f"<CoachAssignment {self.role} coach={self.coach_id} cohort={self.cohort_id}>"


class ShadowEvaluation(Base):
    """Evaluation of a shadow coach by the lead coach after a session.

    Lead coaches fill these out to track shadow progress and
    determine readiness for promotion.
    """

    __tablename__ = "shadow_evaluations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    assignment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("coach_assignments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    evaluator_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )  # Lead coach who evaluated

    session_date: Mapped[datetime] = mapped_column(Date, nullable=False)

    # Scores as JSON: {"communication": 4, "safety": 5, "technique_demo": 3, ...}
    scores: Mapped[dict] = mapped_column(JSON, nullable=False)

    feedback: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    recommendation: Mapped[str] = mapped_column(
        String(30), nullable=False
    )  # "continue_shadow", "ready_for_assistant", "ready_for_lead"

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )

    # Relationships
    assignment = relationship("CoachAssignment", back_populates="evaluations")

    def __repr__(self):
        return f"<ShadowEvaluation assignment={self.assignment_id} rec={self.recommendation}>"
