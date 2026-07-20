"""add session booking reference to payments

Revision ID: a91c4e7d2b60
Revises: 48d7c2a91e6b
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a91c4e7d2b60"
down_revision: Union[str, None] = "48d7c2a91e6b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "payments",
        sa.Column("session_booking_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.execute(
        """
        UPDATE payments
        SET session_booking_id = (metadata ->> 'booking_id')::uuid
        WHERE purpose = 'session_booking'
          AND metadata ->> 'booking_id' IS NOT NULL
          AND metadata ->> 'booking_id' ~*
              '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
        """
    )
    op.create_index(
        "ix_payments_session_booking_id",
        "payments",
        ["session_booking_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_payments_session_booking_id", table_name="payments")
    op.drop_column("payments", "session_booking_id")
