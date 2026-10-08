"""Pool access booking terms acceptance snapshots.

Revision ID: ac20261008f6
Revises: ac20261008e5
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "ac20261008f6"
down_revision = "ac20261008e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pool_access_bookings",
        sa.Column(
            "access_terms_snapshot",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "pool_access_bookings",
        sa.Column("terms_accepted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.alter_column(
        "pool_access_bookings", "access_terms_snapshot", server_default=None
    )


def downgrade() -> None:
    op.drop_column("pool_access_bookings", "terms_accepted_at")
    op.drop_column("pool_access_bookings", "access_terms_snapshot")
