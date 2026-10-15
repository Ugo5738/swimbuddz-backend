"""Club per-enrollment commercial adjustments.

Revision ID: b20261015terms
Revises: 6a1d4c9e7b32
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "b20261015terms"
down_revision = "6a1d4c9e7b32"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table(
        "club_commercial_adjustments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("enrollment_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("club_enrollments.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("source_adjustment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_auth_id", sa.String(length=160), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("original_terms", postgresql.JSONB(), nullable=False),
        sa.Column("approved_terms", postgresql.JSONB(), nullable=False),
        sa.Column("session_fee_kobo", sa.Integer(), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("session_fee_kobo >= 0", name="ck_club_commercial_adjustment_nonnegative"),
        sa.CheckConstraint("valid_until > valid_from", name="ck_club_commercial_adjustment_window"),
    )
    op.create_index("ix_club_commercial_adjustments_enrollment_id", "club_commercial_adjustments", ["enrollment_id"])
    op.create_index("ix_club_commercial_adjustments_enrollment_window", "club_commercial_adjustments", ["enrollment_id","valid_from","valid_until"])

def downgrade() -> None:
    op.drop_index("ix_club_commercial_adjustments_enrollment_window", table_name="club_commercial_adjustments")
    op.drop_index("ix_club_commercial_adjustments_enrollment_id", table_name="club_commercial_adjustments")
    op.drop_table("club_commercial_adjustments")
