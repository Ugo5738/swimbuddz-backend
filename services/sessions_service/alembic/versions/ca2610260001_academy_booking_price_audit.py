"""Add immutable Academy class price decision snapshots to session bookings.

Revision ID: ca2610260001
Revises: f7c2a4d8e901
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "ca2610260001"
down_revision = "f7c2a4d8e901"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "session_bookings",
        sa.Column("academy_price_audit", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("session_bookings", "academy_price_audit")
