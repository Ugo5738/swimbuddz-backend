"""add variant cost price

Revision ID: d4a6c8e0f213
Revises: 8f2b41d30e75
Create Date: 2026-09-06
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d4a6c8e0f213"
down_revision: Union[str, None] = "8f2b41d30e75"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "store_product_variants",
        sa.Column("cost_price_ngn", sa.Numeric(12, 2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("store_product_variants", "cost_price_ngn")
