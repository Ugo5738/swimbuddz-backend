"""allow session participant attendance

Revision ID: c7d8e9f0a1b2
Revises: b5696faef891
Create Date: 2026-10-04
"""

import sqlalchemy as sa
from alembic import op

revision = "c7d8e9f0a1b2"
down_revision = "b5696faef891"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "attendance_records",
        sa.Column("participant_id", sa.UUID(), nullable=True),
    )
    op.create_index(
        "ix_attendance_records_participant_id",
        "attendance_records",
        ["participant_id"],
        unique=False,
    )
    op.create_unique_constraint(
        "uq_session_participant_attendance",
        "attendance_records",
        ["session_id", "participant_id"],
    )
    op.drop_constraint(
        "ck_attendance_member_xor_guest",
        "attendance_records",
        type_="check",
    )
    op.create_check_constraint(
        "ck_attendance_exactly_one_subject",
        "attendance_records",
        "((member_id IS NOT NULL)::int + "
        "(booking_guest_id IS NOT NULL)::int + "
        "(participant_id IS NOT NULL)::int) = 1",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_attendance_exactly_one_subject",
        "attendance_records",
        type_="check",
    )
    op.create_check_constraint(
        "ck_attendance_member_xor_guest",
        "attendance_records",
        "(member_id IS NOT NULL) <> (booking_guest_id IS NOT NULL)",
    )
    op.drop_constraint(
        "uq_session_participant_attendance",
        "attendance_records",
        type_="unique",
    )
    op.drop_index(
        "ix_attendance_records_participant_id",
        table_name="attendance_records",
    )
    op.drop_column("attendance_records", "participant_id")
