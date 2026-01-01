import enum
import uuid
from datetime import datetime
from typing import Optional

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from sqlalchemy import JSON, Boolean, DateTime
from sqlalchemy import Enum as SAEnum
from sqlalchemy import Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship


# ============================================================================
# ENUMS
# ============================================================================


class ProgramLevel(str, enum.Enum):
    BEGINNER_1 = "beginner_1"
    BEGINNER_2 = "beginner_2"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"
    SPECIALTY = "specialty"


class BillingType(str, enum.Enum):
    ONE_TIME = "one_time"
    SUBSCRIPTION = "subscription"
    PER_SESSION = "per_session"


class CohortStatus(str, enum.Enum):
    OPEN = "open"
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class LocationType(str, enum.Enum):
    POOL = "pool"
    OPEN_WATER = "open_water"
    REMOTE = "remote"




class EnrollmentStatus(str, enum.Enum):
    PENDING_APPROVAL = "pending_approval"
    ENROLLED = "enrolled"
    WAITLIST = "waitlist"
    DROPPED = "dropped"
    GRADUATED = "graduated"


class EnrollmentSource(str, enum.Enum):
    WEB = "web"
    ADMIN = "admin"
    PARTNER = "partner"


class PaymentStatus(str, enum.Enum):
    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"
    WAIVED = "waived"


class MilestoneType(str, enum.Enum):
    SKILL = "skill"
    ENDURANCE = "endurance"
    TECHNIQUE = "technique"
    ASSESSMENT = "assessment"


class RequiredEvidence(str, enum.Enum):
    NONE = "none"
    VIDEO = "video"
    TIME_TRIAL = "time_trial"


class ProgressStatus(str, enum.Enum):
    PENDING = "pending"
    ACHIEVED = "achieved"


class ResourceSourceType(str, enum.Enum):
    URL = "url"
    UPLOAD = "upload"


class ResourceVisibility(str, enum.Enum):
    PUBLIC = "public"
    ENROLLED_ONLY = "enrolled_only"
    COACHES_ONLY = "coaches_only"


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
    cover_image_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    level: Mapped[ProgramLevel] = mapped_column(
        SAEnum(ProgramLevel, name="program_level_enum"), nullable=False
    )
    duration_weeks: Mapped[int] = mapped_column(Integer, nullable=False)
    default_capacity: Mapped[int] = mapped_column(
        Integer, default=10, server_default="10"
    )

    # Pricing
    currency: Mapped[str] = mapped_column(String, default="NGN", server_default="NGN")
    price_amount: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )  # In smallest unit (kobo/cents)
    billing_type: Mapped[BillingType] = mapped_column(
        SAEnum(BillingType, name="billing_type_enum"),
        default=BillingType.ONE_TIME,
        server_default="ONE_TIME",
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
        "CurriculumWeek", back_populates="curriculum", order_by="CurriculumWeek.order_index"
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
        "CurriculumLesson", back_populates="week", order_by="CurriculumLesson.order_index"
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
        SAEnum(LocationType, name="location_type_enum"),
        default=LocationType.POOL,
        server_default="POOL",
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
        SAEnum(CohortStatus, name="cohort_status_enum"), default=CohortStatus.OPEN
    )

    allow_mid_entry: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default="false"
    )
    notes_internal: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

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
        UUID(as_uuid=True), ForeignKey("cohorts.id"), nullable=False
    )
    title: Mapped[str] = mapped_column(String, nullable=False)
    resource_type: Mapped[str] = mapped_column(
        String, nullable=False
    )  # 'note', 'drill', 'assignment'
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Source (URL or upload)
    source_type: Mapped[ResourceSourceType] = mapped_column(
        SAEnum(ResourceSourceType, name="resource_source_type_enum"),
        default=ResourceSourceType.URL,
        server_default="URL",
    )
    content_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    storage_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    mime_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    file_size_bytes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Visibility & Organization
    visibility: Mapped[ResourceVisibility] = mapped_column(
        SAEnum(ResourceVisibility, name="resource_visibility_enum"),
        default=ResourceVisibility.ENROLLED_ONLY,
        server_default="ENROLLED_ONLY",
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
        UUID(as_uuid=True), ForeignKey("cohorts.id"), nullable=True
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )

    # User preferences for matching
    preferences: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    status: Mapped[EnrollmentStatus] = mapped_column(
        SAEnum(EnrollmentStatus, name="enrollment_status_enum"),
        default=EnrollmentStatus.PENDING_APPROVAL,
    )

    # Payment tracking
    payment_status: Mapped[PaymentStatus] = mapped_column(
        SAEnum(PaymentStatus, name="academy_payment_status_enum"),
        default=PaymentStatus.PENDING,
    )
    price_snapshot_amount: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    currency_snapshot: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    payment_reference: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    paid_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Enrollment tracking
    enrolled_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    source: Mapped[EnrollmentSource] = mapped_column(
        SAEnum(EnrollmentSource, name="enrollment_source_enum"),
        default=EnrollmentSource.WEB,
        server_default="WEB",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    # Relationships
    program = relationship("Program")
    cohort = relationship("Cohort", back_populates="enrollments")
    progress_records = relationship("StudentProgress", back_populates="enrollment")

    def __repr__(self):
        return f"<Enrollment Member={self.member_id} Cohort={self.cohort_id}>"


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
    video_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # Organization & Type
    order_index: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    milestone_type: Mapped[MilestoneType] = mapped_column(
        SAEnum(MilestoneType, name="milestone_type_enum"),
        default=MilestoneType.SKILL,
        server_default="SKILL",
    )

    # Assessment
    rubric_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    required_evidence: Mapped[RequiredEvidence] = mapped_column(
        SAEnum(RequiredEvidence, name="required_evidence_enum"),
        default=RequiredEvidence.NONE,
        server_default="NONE",
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


class StudentProgress(Base):
    __tablename__ = "student_progress"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    enrollment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("enrollments.id"), nullable=False
    )
    milestone_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("milestones.id"), nullable=False
    )

    status: Mapped[ProgressStatus] = mapped_column(
        SAEnum(ProgressStatus, name="progress_status_enum"),
        default=ProgressStatus.PENDING,
    )
    achieved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Evidence & Scoring
    evidence_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
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
