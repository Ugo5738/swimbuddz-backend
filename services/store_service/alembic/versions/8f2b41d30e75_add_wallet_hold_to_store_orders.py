"""add wallet hold to store orders

Revision ID: 8f2b41d30e75
Revises: 2de3f310326d
Create Date: 2026-07-14
"""

from alembic import op
import sqlalchemy as sa


revision = "8f2b41d30e75"
down_revision = "2de3f310326d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "store_orders", sa.Column("wallet_hold_id", sa.String(length=100), nullable=True)
    )
    op.create_index(
        "ix_store_orders_wallet_hold_id",
        "store_orders",
        ["wallet_hold_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_store_orders_wallet_hold_id", table_name="store_orders")
    op.drop_column("store_orders", "wallet_hold_id")
