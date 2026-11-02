"""Separate regular automatic attendance from individual extra-class attendance.

Revision ID: ca2610270001
Revises: ca2610260001
"""

from alembic import op
import sqlalchemy as sa

revision = "ca2610270001"
down_revision = "ca2610260001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("sessions", "session_templates"):
        op.add_column(
            table,
            sa.Column(
                "cohort_attendance_mode",
                sa.String(24),
                nullable=False,
                server_default="automatic",
            ),
        )
        # Paid extras already have an explicit classification. Included extras
        # need an operator review; names and week numbers are not policy.
        op.execute(
            sa.text(
                f"UPDATE {table} SET cohort_attendance_mode = 'individual' "
                "WHERE cohort_fee_mode = 'paid_extra'"
            )
        )
        op.create_check_constraint(
            f"ck_{table}_cohort_attendance_mode",
            table,
            "cohort_attendance_mode IN ('automatic','individual') AND "
            "(session_type = 'cohort_class' OR cohort_attendance_mode = 'automatic')",
        )


def downgrade() -> None:
    for table in ("session_templates", "sessions"):
        op.drop_constraint(f"ck_{table}_cohort_attendance_mode", table, type_="check")
        op.drop_column(table, "cohort_attendance_mode")
