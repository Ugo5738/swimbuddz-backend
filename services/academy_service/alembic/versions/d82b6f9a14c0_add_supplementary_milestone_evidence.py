"""Add append-only supplementary videos independent of assessed progress.

Revision ID: d82b6f9a14c0
Revises: f6c8e0a2b413
"""
from alembic import op
import sqlalchemy as sa

revision = "d82b6f9a14c0"
down_revision = "f6c8e0a2b413"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "milestone_evidence",
        sa.Column("id", sa.UUID(), primary_key=True, nullable=False),
        sa.Column("enrollment_id", sa.UUID(), sa.ForeignKey("enrollments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("milestone_id", sa.UUID(), sa.ForeignKey("milestones.id", ondelete="CASCADE"), nullable=False),
        sa.Column("video_media_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("caption", sa.Text(), nullable=True),
        sa.Column("recorded_on", sa.Date(), nullable=True),
        sa.Column("consent_to_share", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("approved_for_public", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("public_display_name", sa.String(80), nullable=True),
        sa.Column("publication_consent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("showcase_approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("coach_notes", sa.Text(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("showcase_review_notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("kind IN ('cohort_archive', 'continued_progress')", name="ck_milestone_evidence_kind"),
    )
    op.create_index(
        "ix_milestone_evidence_enrollment_milestone",
        "milestone_evidence",
        ["enrollment_id", "milestone_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_milestone_evidence_enrollment_milestone", table_name="milestone_evidence")
    op.drop_table("milestone_evidence")
