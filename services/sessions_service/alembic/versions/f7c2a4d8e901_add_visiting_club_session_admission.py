"""add_visiting_club_session_admission

Additive session admission controls for members visiting from another Club
location. SessionTemplate stores the same policy inside admission_settings JSONB.

Revision ID: f7c2a4d8e901
Revises: 1c2d3e4f5061
Create Date: 2026-10-04 09:20:00
"""

from alembic import op
import sqlalchemy as sa


revision = "f7c2a4d8e901"
down_revision = "1c2d3e4f5061"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sessions",
        sa.Column(
            "visiting_club_fee_kobo",
            sa.Integer(),
            nullable=True,
        ),
    )
    op.add_column(
        "sessions",
        sa.Column(
            "allows_visiting_club_members",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_check_constraint(
        "ck_sessions_visiting_club_fee_nonnegative",
        "sessions",
        "visiting_club_fee_kobo IS NULL OR visiting_club_fee_kobo >= 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_sessions_visiting_club_fee_nonnegative",
        "sessions",
        type_="check",
    )
    op.drop_column("sessions", "allows_visiting_club_members")
    op.drop_column("sessions", "visiting_club_fee_kobo")
