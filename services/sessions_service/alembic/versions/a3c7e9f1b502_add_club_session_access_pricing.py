"""add Club-session access pricing snapshots

Revision ID: a3c7e9f1b502
Revises: f2c6a8d4e901
Create Date: 2026-09-03

Hand-written migration — includes constraints and data backfills that Alembic
autogenerate cannot safely express.
"""

import sqlalchemy as sa
from alembic import op

revision = "a3c7e9f1b502"
down_revision = "f2c6a8d4e901"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sessions",
        sa.Column(
            "allows_community_dropins",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "session_bookings",
        sa.Column("access_source", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "session_bookings",
        sa.Column(
            "member_fee_amount_kobo",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.create_check_constraint(
        "ck_session_booking_member_fee_nonnegative",
        "session_bookings",
        "member_fee_amount_kobo >= 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_session_booking_member_fee_nonnegative",
        "session_bookings",
        type_="check",
    )
    op.drop_column("session_bookings", "member_fee_amount_kobo")
    op.drop_column("session_bookings", "access_source")
    op.drop_column("sessions", "allows_community_dropins")
