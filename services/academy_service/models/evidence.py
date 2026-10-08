"""Supplementary swimmer evidence, independent from assessed cohort results."""

import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from libs.common.datetime_utils import utc_now
from libs.db.base import Base


class MilestoneEvidence(Base):
    """Alumni and current swimmers can archive videos without changing grades."""

    __tablename__ = "milestone_evidence"
    __table_args__ = (
        Index(
            "ix_milestone_evidence_enrollment_milestone",
            "enrollment_id",
            "milestone_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    enrollment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("enrollments.id", ondelete="CASCADE"),
        nullable=False,
    )
    milestone_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("milestones.id", ondelete="CASCADE"),
        nullable=False,
    )
    video_media_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    caption: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    recorded_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    consent_to_share: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    # Consent alone does not approve public use; admin approval is separate.
    approved_for_public: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    public_display_name: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    publication_consent_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    showcase_approved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    coach_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    showcase_review_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
