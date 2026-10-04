"""add participant id to attendance records

Revision ID: c7d8e9f0a123
Revises: b5696faef891
Create Date: 2026-10-04

Attendance can now point at the canonical sessions_service SessionParticipant.
Legacy member_id / booking_guest_id pointers stay in place during migration.
"""

from alembic import op
import sqlalchemy as sa


revision = "c7d8e9f0a123"
down_revision = "b5696faef891"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_attendance_member_xor_guest",
        "attendance_records",
        type_="check",
    )
    op.add_column(
        "attendance_records",
        sa.Column("participant_id", sa.UUID(), nullable=True),
    )
    op.create_index(
        op.f("ix_attendance_records_participant_id"),
        "attendance_records",
        ["participant_id"],
        unique=False,
    )
    op.create_unique_constraint(
        "uq_session_participant_attendance",
        "attendance_records",
        ["session_id", "participant_id"],
    )
    op.create_check_constraint(
        "ck_attendance_has_subject",
        "attendance_records",
        "member_id IS NOT NULL OR booking_guest_id IS NOT NULL OR participant_id IS NOT NULL",
    )


def downgrade() -> None:
    # A participant-only walk-in cannot be represented by the old schema.
    bind = op.get_bind()
    count = bind.execute(
        sa.text(
            "SELECT count(*) FROM attendance_records "
            "WHERE participant_id IS NOT NULL "
            "AND member_id IS NULL AND booking_guest_id IS NULL"
        )
    ).scalar_one()
    if count:
        raise RuntimeError(
            "Reconcile participant-only attendance before downgrading; "
            "the legacy schema cannot represent unregistered walk-ins"
        )

    op.drop_constraint(
        "ck_attendance_has_subject",
        "attendance_records",
        type_="check",
    )
    op.drop_constraint(
        "uq_session_participant_attendance",
        "attendance_records",
        type_="unique",
    )
    op.drop_index(
        op.f("ix_attendance_records_participant_id"),
        table_name="attendance_records",
    )
    op.drop_column("attendance_records", "participant_id")
    op.create_check_constraint(
        "ck_attendance_member_xor_guest",
        "attendance_records",
        "(member_id IS NOT NULL) <> (booking_guest_id IS NOT NULL)",
    )
