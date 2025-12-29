import enum
import uuid
from datetime import datetime

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from sqlalchemy import JSON, Boolean, DateTime
from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship


class ProgramLevel(str, enum.Enum):
    BEGINNER_1 = "beginner_1"
    BEGINNER_2 = "beginner_2"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"
    SPECIALTY = "specialty"


class MemberRef(Base):
    """Reference to shared members table without cross-service imports."""

    __tablename__ = "members"
    __table_args__ = {"extend_existing": True, "info": {"skip_autogenerate": True}}

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )


class CohortStatus(str, enum.Enum):
    OPEN = "open"
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class EnrollmentStatus(str, enum.Enum):
    PENDING_APPROVAL = "pending_approval"
    ENROLLED = "enrolled"
    WAITLIST = "waitlist"
    DROPPED = "dropped"
    GRADUATED = "graduated"


class PaymentStatus(str, enum.Enum):
    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"
    WAIVED = "waived"


class ProgressStatus(str, enum.Enum):
    PENDING = "pending"
    ACHIEVED = "achieved"


class Program(Base):
    __tablename__ = "programs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    level: Mapped[ProgramLevel] = mapped_column(
        SAEnum(ProgramLevel, name="program_level_enum"), nullable=False
    )
    duration_weeks: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[int] = mapped_column(
        Integer, default=0
    )  # Stored in lowest currency unit (e.g. kobo/cents) or just plain amount? Let's assume plain integer for now or 0 if free.
    curriculum_json: Mapped[dict] = mapped_column(JSON, nullable=True)
    prep_materials: Mapped[dict] = mapped_column(
        JSON, nullable=True
    )  # links, docs, etc.

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    # Relationships
    cohorts = relationship("Cohort", back_populates="program")
    milestones = relationship("Milestone", back_populates="program")

    def __repr__(self):
        return f"<Program {self.name}>"


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

    # Coach - References Member ID (who has a CoachProfile)
    # We reference Member ID because that's the primary identity.
    coach_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=True)

    status: Mapped[CohortStatus] = mapped_column(
        SAEnum(CohortStatus, name="cohort_status_enum"), default=CohortStatus.OPEN
    )

    allow_mid_entry: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default="false"
    )

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
    content_url: Mapped[str] = mapped_column(String, nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )

    # Relationships
    cohort = relationship("Cohort", back_populates="resources")


class Enrollment(Base):
    __tablename__ = "enrollments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Program is now required for the request
    program_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("programs.id"),
        nullable=True,  # Nullable for now to ease migration, or we backfill? strict: True if possible.
        # Actually, let's make it nullable=True initially to avoid migration headaches with existing data if we can't easily backfill in one go,
        # BUT logically it should be False.
        # I'll set nullable=True for safety, and we can enforce in code or backfill later.
    )

    # Cohort is optional initially (until assigned)
    cohort_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cohorts.id"), nullable=True
    )

    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )

    # Store user preferences for matching (Time, Location, etc)
    preferences: Mapped[dict] = mapped_column(JSON, nullable=True)

    status: Mapped[EnrollmentStatus] = mapped_column(
        SAEnum(EnrollmentStatus, name="enrollment_status_enum"),
        default=EnrollmentStatus.PENDING_APPROVAL,
    )
    payment_status: Mapped[PaymentStatus] = mapped_column(
        SAEnum(PaymentStatus, name="academy_payment_status_enum"),
        default=PaymentStatus.PENDING,
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


class Milestone(Base):
    __tablename__ = "milestones"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    program_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("programs.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    criteria: Mapped[str] = mapped_column(Text, nullable=True)
    video_url: Mapped[str] = mapped_column(String, nullable=True)

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
    achieved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    coach_notes: Mapped[str] = mapped_column(Text, nullable=True)

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
