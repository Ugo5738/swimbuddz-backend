"""Add interval and calendar recurrence rules to Session templates.

Revision ID: e7f9a1b3c425
Revises: c6e8a0b2d914
"""

from alembic import op
import sqlalchemy as sa


revision = "e7f9a1b3c425"
down_revision = "c6e8a0b2d914"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "session_templates",
        sa.Column("frequency", sa.String(16), nullable=False, server_default="weekly"),
    )
    op.add_column(
        "session_templates",
        sa.Column("interval", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "session_templates", sa.Column("week_of_month", sa.Integer(), nullable=True)
    )
    op.add_column(
        "session_templates", sa.Column("day_of_month", sa.Integer(), nullable=True)
    )
    op.add_column(
        "session_templates", sa.Column("month_of_year", sa.Integer(), nullable=True)
    )
    op.add_column(
        "session_templates",
        sa.Column(
            "starts_on", sa.Date(), nullable=False, server_default=sa.text("CURRENT_DATE")
        ),
    )
    op.add_column(
        "session_templates", sa.Column("ends_on", sa.Date(), nullable=True)
    )
    op.create_check_constraint(
        "ck_session_templates_recurrence",
        "session_templates",
        "frequency IN ('weekly','monthly','quarterly','annual') "
        "AND interval >= 1 "
        "AND (week_of_month IS NULL OR week_of_month IN (-1,1,2,3,4,5)) "
        "AND (day_of_month IS NULL OR day_of_month BETWEEN 1 AND 31) "
        "AND (month_of_year IS NULL OR month_of_year BETWEEN 1 AND 12) "
        "AND (ends_on IS NULL OR ends_on >= starts_on) "
        "AND (frequency <> 'weekly' OR week_of_month IS NULL)",
    )


def downgrade():
    op.drop_constraint(
        "ck_session_templates_recurrence", "session_templates", type_="check"
    )
    for column in (
        "ends_on",
        "starts_on",
        "month_of_year",
        "day_of_month",
        "week_of_month",
        "interval",
        "frequency",
    ):
        op.drop_column("session_templates", column)
