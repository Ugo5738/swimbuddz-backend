"""Immutable, portable Academy competency verification history.

This ledger is separate from legacy StudentProgress and certificate decisions.
A successful verification is never edited; an explicit revocation is appended
instead. Read projections also check whether legacy source approval remains valid.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from libs.common.datetime_utils import utc_now
from libs.db.base import Base


class AcademyCompetencyVerification(Base):
    __tablename__ = "academy_competency_verifications"
    __table_args__ = (
        UniqueConstraint(
            "source_review_event_id",
            name="uq_acv_source_review",
        ),
        Index(
            "ix_acv_member_auth_milestone",
            "member_auth_id",
            "milestone_definition_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    member_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    member_auth_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    # Source IDs remain even if an old enrollment is later deleted. The
    # verification snapshot is still traceable, but its current status
    # becomes "recheck_required" when the source disappears.
    source_enrollment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    source_progress_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    source_review_event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    milestone_definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("academy_milestone_definitions.id"),
        nullable=False,
    )
    milestone_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("academy_milestone_versions.id"),
        nullable=False,
    )
    rubric_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    criteria_passed: Mapped[list] = mapped_column(JSONB, nullable=False)
    assessment_context_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    evidence_media_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    evidence_owner_auth_id: Mapped[str] = mapped_column(String(160), nullable=False)
    verified_by_auth_id: Mapped[str] = mapped_column(String(160), nullable=False)
    review_notes: Mapped[str] = mapped_column(Text, nullable=False)
    verified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class AcademyCompetencyRevocation(Base):
    __tablename__ = "academy_competency_revocations"
    __table_args__ = (UniqueConstraint("verification_id", name="uq_acr_verification"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    verification_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("academy_competency_verifications.id"),
        nullable=False,
        index=True,
    )
    revoked_by_auth_id: Mapped[str] = mapped_column(String(160), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    revoked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
