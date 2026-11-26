"""Immutable, portable Academy competency verification history and TI drill prescriptions.

The verification ledger is separate from legacy StudentProgress and certificate decisions.
A successful verification is never edited; an explicit revocation is appended instead.
Supported source_type values:
- 'legacy_promotion': Admin-reviewed promotion of a historical StudentProgress approval.
- 'cohort_v2': Direct coach/admin assessment against a cohort's frozen V2 snapshot.
- 'diagnostic_assessment': Pre-enrollment/standalone diagnostic clinic assessment.

TI remedial drill mappings (academy_milestone_remedial_drills) and personalized student
drill assignments (academy_student_drill_assignments) connect milestones to coaching
practice without ever granting practical competency credentials automatically.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
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
        CheckConstraint(
            "source_type IN ('legacy_promotion','cohort_v2','diagnostic_assessment')",
            name="ck_acv_source_type",
        ),
        CheckConstraint(
            "assessor_role IN ('coach','admin')",
            name="ck_acv_assessor_role",
        ),
        CheckConstraint(
            "(source_type = 'legacy_promotion' "
            "AND source_enrollment_id IS NOT NULL "
            "AND source_progress_id IS NOT NULL "
            "AND source_review_event_id IS NOT NULL) "
            "OR (source_type = 'cohort_v2' "
            "AND source_enrollment_id IS NOT NULL "
            "AND source_progress_id IS NULL "
            "AND source_review_event_id IS NULL) "
            "OR (source_type = 'diagnostic_assessment' "
            "AND source_enrollment_id IS NULL "
            "AND source_progress_id IS NULL "
            "AND source_review_event_id IS NULL)",
            name="ck_acv_source_provenance",
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
    source_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="legacy_promotion",
        server_default="legacy_promotion",
    )
    assessor_role: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="admin",
        server_default="admin",
    )
    # Source IDs remain untied (no FK to enrollments/student_progress) so append-only
    # ledger integrity survives enrollment deletion. Nullable for native V2 assessments.
    source_enrollment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    source_progress_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    source_review_event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
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


class AcademyMilestoneRemedialDrill(Base):
    """Links a canonical V2 milestone definition to recommended TI remedial drills."""

    __tablename__ = "academy_milestone_remedial_drills"
    __table_args__ = (
        UniqueConstraint(
            "milestone_definition_id",
            "drill_id",
            name="uq_amrd_milestone_drill",
        ),
        CheckConstraint("priority_order >= 0", name="ck_amrd_priority_nonneg"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    milestone_definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("academy_milestone_definitions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    drill_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("academy_drills.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    priority_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    coach_tip: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class AcademyStudentDrillAssignment(Base):
    """Personalized, version-pinned TI drill prescription for a swimmer."""

    __tablename__ = "academy_student_drill_assignments"
    __table_args__ = (
        CheckConstraint(
            "assigned_by_role IN ('coach','admin','strokelab_ai')",
            name="ck_asda_assigned_by_role",
        ),
        CheckConstraint(
            "status IN ('assigned','practiced','mastered','archived')",
            name="ck_asda_status",
        ),
        CheckConstraint("sets > 0", name="ck_asda_sets_positive"),
        CheckConstraint(
            "distance_m IS NULL OR distance_m > 0",
            name="ck_asda_distance_positive",
        ),
        Index("ix_asda_member_auth_status", "member_auth_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    member_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    member_auth_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    enrollment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("enrollments.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    drill_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("academy_drill_versions.id"),
        nullable=False,
        index=True,
    )
    milestone_definition_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("academy_milestone_definitions.id", ondelete="SET NULL"),
        nullable=True,
    )
    assigned_by_auth_id: Mapped[str] = mapped_column(String(160), nullable=False)
    assigned_by_role: Mapped[str] = mapped_column(String(20), nullable=False)
    focal_cue_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    sets: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3, server_default="3"
    )
    distance_m: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="assigned", server_default="assigned"
    )
    practiced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )
