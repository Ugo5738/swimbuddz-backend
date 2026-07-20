"""add booking attribution

Revision ID: 7de6c9f14a20
Revises: 9f3a8d7c1b20
Create Date: 2026-07-17
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "7de6c9f14a20"
down_revision: Union[str, None] = "9f3a8d7c1b20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("session_bookings", sa.Column("booking_source", sa.Text()))
    op.add_column("session_bookings", sa.Column("campaign_key", sa.Text()))
    op.create_index(
        "ix_session_bookings_campaign_key",
        "session_bookings",
        ["campaign_key"],
    )


def downgrade() -> None:
    op.drop_index("ix_session_bookings_campaign_key", table_name="session_bookings")
    op.drop_column("session_bookings", "campaign_key")
    op.drop_column("session_bookings", "booking_source")
