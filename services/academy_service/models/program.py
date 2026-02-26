import uuid
from datetime import datetime
from typing import Optional

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from services.academy_service.models.enums import BillingType, ProgramLevel, enum_values
from sqlalchemy import JSON, Boolean, DateTime
from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Integer, String, Text
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
    )  # Kobo (minor NGN unit)
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
