"""Auditable per-enrollment commercial revisions, separate from cohort transfers."""

import uuid

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from libs.common.datetime_utils import utc_now
from libs.db.base import Base


class AcademyCommercialAdjustment(Base):
    __tablename__ = "academy_commercial_adjustments"

    # Client-generated UUID provides exact retry idempotency across processes.
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    enrollment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("enrollments.id"),
        nullable=False,
        index=True,
    )
    actor_auth_id: Mapped[str] = mapped_column(String(160), nullable=False)
    reason: Mapped[str] = mapped_column(Text(), nullable=False)
    original_terms: Mapped[dict] = mapped_column(JSON, nullable=False)
    approved_terms: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
