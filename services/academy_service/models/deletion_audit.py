"""Immutable audit entries for safely removed empty Academy enrollments."""
import uuid
from sqlalchemy import DateTime, JSON, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from libs.common.datetime_utils import utc_now
from libs.db.base import Base

class AcademyEnrollmentDeletionAudit(Base):
    __tablename__ = "academy_enrollment_deletion_audits"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    enrollment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, unique=True, index=True)
    member_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    member_auth_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    actor_auth_id: Mapped[str] = mapped_column(String(160), nullable=False)
    reason: Mapped[str] = mapped_column(Text(), nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    deleted_at = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
