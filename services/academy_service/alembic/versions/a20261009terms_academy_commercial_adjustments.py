"""Audit negotiated enrollment terms without rewriting payment history.

Revision ID: a20261009terms
Revises: a20261008credit
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "a20261009terms"
down_revision = "a20261008credit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "academy_commercial_adjustments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "enrollment_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("enrollments.id"), nullable=False
        ),
        sa.Column("actor_auth_id", sa.String(160), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("original_terms", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("approved_terms", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_academy_commercial_adjustments_enrollment_id",
        "academy_commercial_adjustments", ["enrollment_id"]
    )


def downgrade() -> None:
    op.drop_index(
        "ix_academy_commercial_adjustments_enrollment_id",
        table_name="academy_commercial_adjustments"
    )
    op.drop_table("academy_commercial_adjustments")
