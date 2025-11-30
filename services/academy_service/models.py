import enum
import uuid
from datetime import datetime

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


class CohortStatus(str, enum.Enum):
    OPEN = "open"
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class EnrollmentStatus(str, enum.Enum):
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


class Member(Base):
    __tablename__ = "members"
    __table_args__ = {"extend_existing": True}

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    def __repr__(self) -> str:
        return f"<Member {self.id}>"


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
    curriculum_json: Mapped[dict] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
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
    status: Mapped[CohortStatus] = mapped_column(
        SAEnum(CohortStatus, name="cohort_status_enum"), default=CohortStatus.OPEN
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Relationships
    program = relationship("Program", back_populates="cohorts")
    enrollments = relationship("Enrollment", back_populates="cohort")

    def __repr__(self):
        return f"<Cohort {self.name} ({self.status})>"


class Enrollment(Base):
    __tablename__ = "enrollments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    cohort_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cohorts.id"), nullable=False
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("members.id"), nullable=False, index=True
    )  # Reference to Member in members_service

    status: Mapped[EnrollmentStatus] = mapped_column(
        SAEnum(EnrollmentStatus, name="enrollment_status_enum"),
        default=EnrollmentStatus.ENROLLED,
    )
    payment_status: Mapped[PaymentStatus] = mapped_column(
        SAEnum(PaymentStatus, name="academy_payment_status_enum"),
        default=PaymentStatus.PENDING,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Relationships
    cohort = relationship("Cohort", back_populates="enrollments")
    member = relationship("Member")
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
        DateTime(timezone=True), default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
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
        DateTime(timezone=True), default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Relationships
    enrollment = relationship("Enrollment", back_populates="progress_records")
    milestone = relationship("Milestone")

    def __repr__(self):
        return f"<StudentProgress Enrollment={self.enrollment_id} Milestone={self.milestone_id}>"
