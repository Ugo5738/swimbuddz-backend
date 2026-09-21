"""Preserve the cohort and explicit billing mode when generating Academy classes.

Revision ID: f8a0b2c4d637
Revises: c6e8a0b2d914

Do not infer a cohort or paid-extra status from old titles or prices. Existing
Academy templates remain readable/archivable but must be configured to generate.
Existing sessions, bookings and payments are untouched.
"""

from alembic import op
import sqlalchemy as sa

revision = "f8a0b2c4d637"
down_revision = "c6e8a0b2d914"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("session_templates", sa.Column("cohort_id", sa.UUID(), nullable=True))
    op.add_column(
        "session_templates",
        sa.Column(
            "cohort_fee_mode", sa.String(24), nullable=False, server_default="included"
        ),
    )
    op.create_index(
        "ix_session_templates_cohort_id", "session_templates", ["cohort_id"]
    )
    op.create_check_constraint(
        "ck_session_templates_cohort_context",
        "session_templates",
        "cohort_fee_mode IN ('included','paid_extra') AND (session_type = 'cohort_class' OR (cohort_id IS NULL AND cohort_fee_mode = 'included'))",
    )


def downgrade():
    op.drop_constraint(
        "ck_session_templates_cohort_context", "session_templates", type_="check"
    )
    op.drop_index("ix_session_templates_cohort_id", table_name="session_templates")
    op.drop_column("session_templates", "cohort_fee_mode")
    op.drop_column("session_templates", "cohort_id")
