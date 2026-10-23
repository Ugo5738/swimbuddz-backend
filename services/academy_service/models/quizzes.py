"""Reusable Academy quizzes, cohort-week assignments and immutable graded attempts."""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from libs.common.datetime_utils import utc_now
from libs.db.base import Base


class AcademyQuiz(Base):
    __tablename__ = "academy_quizzes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    program_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("programs.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    questions_json: Mapped[list] = mapped_column(JSONB, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)


class AcademyQuizAssignment(Base):
    __tablename__ = "academy_quiz_assignments"
    __table_args__ = (UniqueConstraint("cohort_id", "quiz_id", "week_number", name="uq_academy_quiz_cohort_week"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    cohort_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("cohorts.id", ondelete="CASCADE"), nullable=False, index=True)
    quiz_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("academy_quizzes.id"), nullable=False)
    week_number: Mapped[int] = mapped_column(Integer, nullable=False)
    passing_score: Mapped[int] = mapped_column(Integer, nullable=False, default=80)
    max_attempts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Freeze the question content and answer key at assignment time.
    questions_snapshot: Mapped[list] = mapped_column(JSONB, nullable=False)
    quiz_version: Mapped[int] = mapped_column(Integer, nullable=False)
    title_snapshot: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class AcademyQuizAttempt(Base):
    __tablename__ = "academy_quiz_attempts"
    __table_args__ = (UniqueConstraint("assignment_id", "enrollment_id", "attempt_number", name="uq_academy_quiz_attempt_number"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    assignment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("academy_quiz_assignments.id", ondelete="RESTRICT"), nullable=False, index=True)
    enrollment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("enrollments.id", ondelete="CASCADE"), nullable=False, index=True)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    answers_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    feedback_snapshot: Mapped[list] = mapped_column(JSONB, nullable=False)
    score_percent: Mapped[int] = mapped_column(Integer, nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
