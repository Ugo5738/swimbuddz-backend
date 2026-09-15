"""Distinguish tuition-included cohort classes from separately paid extras.

Revision ID: c6e8a0b2d914
Revises: b4d8f0a2c613

All existing rows remain included; do not infer billing from titles, week
numbers or nonzero pool costs. Admin explicitly opts genuine extra classes
into paid_extra. Booking/payment snapshots are never rewritten here.
"""

from alembic import op
import sqlalchemy as sa

revision = "c6e8a0b2d914"
down_revision = "b4d8f0a2c613"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "sessions",
        sa.Column(
            "cohort_fee_mode", sa.String(24), nullable=False, server_default="included"
        ),
    )
    op.create_check_constraint(
        "ck_sessions_cohort_fee_mode",
        "sessions",
        "cohort_fee_mode IN ('included','paid_extra') AND (session_type = 'cohort_class' OR cohort_fee_mode = 'included')",
    )


def downgrade():
    op.drop_constraint("ck_sessions_cohort_fee_mode", "sessions", type_="check")
    op.drop_column("sessions", "cohort_fee_mode")
