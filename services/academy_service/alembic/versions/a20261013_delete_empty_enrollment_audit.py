"""Audit allowed deletion of empty, never-financially-active enrollments.

Revision ID: a20261013del
Revises: a20261009terms
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "a20261013del"
down_revision = "a20261009terms"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table(
        "academy_enrollment_deletion_audits",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("enrollment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("member_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("member_auth_id", sa.String(160)),
        sa.Column("actor_auth_id", sa.String(160), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("enrollment_id", name="uq_academy_enrollment_deletion_audit_enrollment"),
    )
    op.create_index("ix_academy_enrollment_deletion_audits_enrollment_id",
        "academy_enrollment_deletion_audits", ["enrollment_id"])

def downgrade():
    op.drop_index("ix_academy_enrollment_deletion_audits_enrollment_id",
        table_name="academy_enrollment_deletion_audits")
    op.drop_table("academy_enrollment_deletion_audits")
