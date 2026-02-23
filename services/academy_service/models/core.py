import uuid
from datetime import datetime
from typing import Optional

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from services.academy_service.models.enums import (
    BillingType,
    CohortStatus,
    EnrollmentSource,
    EnrollmentStatus,
    InstallmentStatus,
    LocationType,
    MilestoneType,
    PaymentStatus,
    ProgramLevel,
    ProgressStatus,
    RequiredEvidence,
    ResourceSourceType,
    ResourceVisibility,
    enum_values,
)
from sqlalchemy import JSON, Boolean, Date, DateTime
from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

# ============================================================================
# REFERENCE MODELS
# ============================================================================


class MemberRef(Base):
    """Reference to shared members table without cross-service imports."""

    __tablename__ = "members"
    __table_args__ = {"extend_existing": True, "info": {"skip_autogenerate": True}}

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )


# ============================================================================
# PROGRAM MODELS
# ============================================================================


class Program(Base):
    __tablename__ = "programs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    slug: Mapped[Optional[str]] = mapped_column(String, unique=True, nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    cover_image_media_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )  # FK to media_service.media_items

    level: Mapped[ProgramLevel] = mapped_column(
        SAEnum(
            ProgramLevel,
            name="program_level_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        nullable=False,
    )
    duration_weeks: Mapped[int] = mapped_column(Integer, nullable=False)
    default_capacity: Mapped[int] = mapped_column(
        Integer, default=10, server_default="10"
    )

    # Pricing
    currency: Mapped[str] = mapped_column(String, default="NGN", server_default="NGN")
    price_amount: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )  # In naira (major unit) - payment service converts to kobo for Paystack
    billing_type: Mapped[BillingType] = mapped_column(
        SAEnum(
            BillingType,
            name="billing_type_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        default=BillingType.ONE_TIME,
        server_default="one_time",
    )

    # Content
    curriculum_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    prep_materials: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Versioning & Status
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    is_published: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    # Relationships
    cohorts = relationship("Cohort", back_populates="program")
    milestones = relationship("Milestone", back_populates="program")
    curricula = relationship("ProgramCurriculum", back_populates="program")

    def __repr__(self):
        return f"<Program {self.name}>"


# ============================================================================
# NORMALIZED CURRICULUM MODELS
# ============================================================================


class ProgramCurriculum(Base):
    """Versioned curriculum for a program."""

    __tablename__ = "program_curricula"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    program_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("programs.id"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    # Relationships
    program = relationship("Program", back_populates="curricula")
    weeks = relationship(
        "CurriculumWeek",
        back_populates="curriculum",
        order_by="CurriculumWeek.order_index",
    )


class CurriculumWeek(Base):
    """Week within a curriculum."""

    __tablename__ = "curriculum_weeks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    curriculum_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("program_curricula.id"), nullable=False
    )
    week_number: Mapped[int] = mapped_column(Integer, nullable=False)
    theme: Mapped[str] = mapped_column(String, nullable=False)
    objectives: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    order_index: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )

    # Relationships
    curriculum = relationship("ProgramCurriculum", back_populates="weeks")
    lessons = relationship(
        "CurriculumLesson",
        back_populates="week",
        order_by="CurriculumLesson.order_index",
    )


class CurriculumLesson(Base):
    """Lesson within a week."""

    __tablename__ = "curriculum_lessons"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    week_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("curriculum_weeks.id"), nullable=False
    )
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    duration_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    order_index: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    video_media_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )  # FK to media_service.media_items - Instructional video

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )

    # Relationships
    week = relationship("CurriculumWeek", back_populates="lessons")
    skills = relationship("LessonSkill", back_populates="lesson")


class Skill(Base):
    """Reusable skill library."""

    __tablename__ = "skills"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    category: Mapped[str] = mapped_column(
        String, nullable=False
    )  # "water_confidence", "stroke", "safety"
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )


class LessonSkill(Base):
    """Junction: which skills a lesson teaches."""

    __tablename__ = "lesson_skills"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    lesson_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("curriculum_lessons.id"), nullable=False
    )
    skill_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("skills.id"), nullable=False
    )

    # Relationships
    lesson = relationship("CurriculumLesson", back_populates="skills")
    skill = relationship("Skill")


# ============================================================================
# COHORT MODELS
# ============================================================================


class Cohort(Base):
    __tablename__ = "cohorts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    program_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("programs.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String, nullable=False)  # e.g. "Jan 2026"

    start_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    end_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    capacity: Mapped[int] = mapped_column(Integer, default=10)

    # Location
    timezone: Mapped[str] = mapped_column(
        String, default="Africa/Lagos", server_default="Africa/Lagos"
    )
    location_type: Mapped[LocationType] = mapped_column(
        SAEnum(
            LocationType,
            name="location_type_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        default=LocationType.POOL,
        server_default="pool",
    )
    location_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    location_address: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # Coach - References Member ID (who has a CoachProfile)
    coach_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    # Pricing override
    price_override: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    status: Mapped[CohortStatus] = mapped_column(
        SAEnum(
            CohortStatus,
            name="cohort_status_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        default=CohortStatus.OPEN,
    )

    allow_mid_entry: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default="false"
    )
    mid_entry_cutoff_week: Mapped[int] = mapped_column(
        Integer, default=2, nullable=False, server_default="2"
    )
    require_approval: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default="false"
    )
    # If True, admin must manually approve every dropout instead of it being automatic.
    # When enabled, enrollment moves to DROPOUT_PENDING at missed_count=2 and waits for admin action.
    admin_dropout_approval: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default="false"
    )
    notes_internal: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # ── Installment billing ──────────────────────────────────────────────────
    # When enabled, members can choose to pay in installments at checkout.
    # The admin sets the number of installments and the deposit (first payment).
    # Remaining installments are auto-debited from the member's wallet on schedule.
    installment_plan_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default="false"
    )
    # Number of total installments (e.g. 3 means deposit + 2 follow-up payments)
    installment_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Deposit amount in kobo. If null, defaults to (total_price / installment_count).
    installment_deposit_amount: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )

    # Coach grade requirement (derived from complexity score)
    # Values: "grade_1", "grade_2", "grade_3" (stored as strings for compatibility)
    required_coach_grade: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    # Relationships
    program = relationship("Program", back_populates="cohorts")
    enrollments = relationship("Enrollment", back_populates="cohort")
    resources = relationship("CohortResource", back_populates="cohort")
    complexity_score = relationship(
        "CohortComplexityScore", back_populates="cohort", uselist=False
    )

    def __repr__(self):
        return f"<Cohort {self.name} ({self.status})>"


# ============================================================================
# RESOURCE MODELS
# ============================================================================


class CohortResource(Base):
    __tablename__ = "cohort_resources"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    cohort_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cohorts.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String, nullable=False)
    resource_type: Mapped[str] = mapped_column(
        String, nullable=False
    )  # 'note', 'drill', 'assignment'
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Source (URL or upload)
    source_type: Mapped[ResourceSourceType] = mapped_column(
        SAEnum(
            ResourceSourceType,
            name="resource_source_type_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        default=ResourceSourceType.URL,
        server_default="url",
    )
    content_media_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )  # FK to media_service.media_items
    storage_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    mime_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    file_size_bytes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Visibility & Organization
    visibility: Mapped[ResourceVisibility] = mapped_column(
        SAEnum(
            ResourceVisibility,
            name="resource_visibility_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        default=ResourceVisibility.ENROLLED_ONLY,
        server_default="enrolled_only",
    )
    week_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )

    # Relationships
    cohort = relationship("Cohort", back_populates="resources")


# ============================================================================
# ENROLLMENT MODELS
# ============================================================================


class Enrollment(Base):
    __tablename__ = "enrollments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    program_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("programs.id"),
        nullable=True,
    )
    cohort_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cohorts.id", ondelete="CASCADE"), nullable=True
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    # Auth ID for ownership verification (avoids cross-service calls)
    member_auth_id: Mapped[Optional[str]] = mapped_column(
        String, nullable=True, index=True
    )

    # User preferences for matching
    preferences: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    status: Mapped[EnrollmentStatus] = mapped_column(
        SAEnum(
            EnrollmentStatus,
            name="enrollment_status_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        default=EnrollmentStatus.PENDING_APPROVAL,
    )

    # Payment tracking
    payment_status: Mapped[PaymentStatus] = mapped_column(
        SAEnum(
            PaymentStatus,
            name="academy_payment_status_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        default=PaymentStatus.PENDING,
    )
    price_snapshot_amount: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    currency_snapshot: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    payment_reference: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    paid_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    total_installments: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, server_default="0"
    )
    paid_installments_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, server_default="0"
    )
    missed_installments_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, server_default="0"
    )
    access_suspended: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default="false"
    )

    # Enrollment tracking
    enrolled_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    source: Mapped[EnrollmentSource] = mapped_column(
        SAEnum(
            EnrollmentSource,
            name="enrollment_source_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        default=EnrollmentSource.WEB,
        server_default="web",
    )

    # Notification tracking
    reminders_sent: Mapped[list] = mapped_column(JSON, default=[], server_default="[]")

    # Certificate tracking
    certificate_issued_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    certificate_code: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    # Relationships
    program = relationship("Program")
    cohort = relationship("Cohort", back_populates="enrollments")
    installments = relationship(
        "EnrollmentInstallment",
        back_populates="enrollment",
        order_by="EnrollmentInstallment.installment_number",
        cascade="all, delete-orphan",
    )
    progress_records = relationship("StudentProgress", back_populates="enrollment")

    def __repr__(self):
        return f"<Enrollment Member={self.member_id} Cohort={self.cohort_id}>"


class EnrollmentInstallment(Base):
    __tablename__ = "enrollment_installments"
    __table_args__ = (
        UniqueConstraint(
            "enrollment_id",
            "installment_number",
            name="uq_enrollment_installment_number",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    enrollment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("enrollments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    installment_number: Mapped[int] = mapped_column(Integer, nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[InstallmentStatus] = mapped_column(
        SAEnum(
            InstallmentStatus,
            name="installment_status_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        nullable=False,
        default=InstallmentStatus.PENDING,
        server_default="pending",
    )
    paid_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    payment_reference: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    enrollment = relationship("Enrollment", back_populates="installments")

    def __repr__(self):
        return f"<EnrollmentInstallment Enrollment={self.enrollment_id} No={self.installment_number} Status={self.status}>"


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
