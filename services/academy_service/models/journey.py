"""Durable Academy journey and cohort-change audit, independent of an enrollment."""
import uuid
from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from libs.db.base import Base
from libs.common.datetime_utils import utc_now

class AcademyJourney(Base):
    __tablename__ = "academy_journeys"
    __table_args__ = (UniqueConstraint("member_id", "program_id", name="uq_academy_journey_member_program"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    member_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    program_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("programs.id"), nullable=False)
    created_at = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

class AcademyEnrollmentChange(Base):
    __tablename__ = "academy_enrollment_changes"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    journey_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("academy_journeys.id"), nullable=False, index=True)
    from_enrollment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("enrollments.id"), nullable=False)
    to_enrollment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("enrollments.id"), nullable=True)
    target_cohort_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("cohorts.id"), nullable=False)
    actor_auth_id: Mapped[str] = mapped_column(String, nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False)  # completed / needs_review
    snapshot = mapped_column(JSON, nullable=False)
    created_at = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
