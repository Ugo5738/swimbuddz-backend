"""Prevent duplicate Pool Access payment initialization.

Revision ID: ac20261008c3
Revises: ac20261008b2
"""

from alembic import op
import sqlalchemy as sa

revision = "ac20261008c3"
down_revision = "ac20261008b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pool_access_bookings",
        sa.Column("checkout_reference", sa.String(160), nullable=True),
    )
    op.create_unique_constraint(
        "uq_pool_access_checkout_reference",
        "pool_access_bookings",
        ["checkout_reference"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_pool_access_checkout_reference", "pool_access_bookings", type_="unique"
    )
    op.drop_column("pool_access_bookings", "checkout_reference")
