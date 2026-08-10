"""add pool street address

Revision ID: c3b752a71f42
Revises: b71c2a95e604
Create Date: 2026-08-10
"""

import sqlalchemy as sa
from alembic import op


revision = "c3b752a71f42"
down_revision = "b71c2a95e604"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("pools", sa.Column("address", sa.String(length=500), nullable=True))


def downgrade() -> None:
    op.drop_column("pools", "address")
