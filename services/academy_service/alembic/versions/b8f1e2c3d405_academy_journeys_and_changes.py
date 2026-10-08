"""Academy journey identity and immutable cohort change records.

Revision ID: b8f1e2c3d405
Revises: f6c8e0a2b413
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "b8f1e2c3d405"
down_revision = "f6c8e0a2b413"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("academy_journeys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("member_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("program_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("programs.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("member_id", "program_id", name="uq_academy_journey_member_program"),
    )
    op.create_index("ix_academy_journeys_member_id", "academy_journeys", ["member_id"])
    op.create_table("academy_enrollment_changes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("journey_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("academy_journeys.id"), nullable=False),
        sa.Column("from_enrollment_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("enrollments.id"), nullable=False),
        sa.Column("to_enrollment_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("enrollments.id"), nullable=True),
        sa.Column("target_cohort_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("cohorts.id"), nullable=False),
        sa.Column("actor_auth_id", sa.String(), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_academy_enrollment_changes_journey_id", "academy_enrollment_changes", ["journey_id"])

def downgrade():
    op.drop_index("ix_academy_enrollment_changes_journey_id", table_name="academy_enrollment_changes")
    op.drop_table("academy_enrollment_changes")
    op.drop_index("ix_academy_journeys_member_id", table_name="academy_journeys")
    op.drop_table("academy_journeys")
