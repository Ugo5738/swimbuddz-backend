"""Add per-variant allow_backorder flag ("Continue selling when out of stock").

Revision ID: h20261127bko
Revises: g20261125ord
"""

import sqlalchemy as sa
from alembic import op

revision = "h20261127bko"
down_revision = "g20261125ord"
branch_labels = None
depends_on = None


def _existing_columns(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(table_name):
        return set()
    return {col["name"] for col in inspector.get_columns(table_name)}


def upgrade() -> None:
    if "allow_backorder" not in _existing_columns("store_product_variants"):
        op.add_column(
            "store_product_variants",
            sa.Column(
                "allow_backorder",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
        )


def downgrade() -> None:
    if "allow_backorder" in _existing_columns("store_product_variants"):
        op.drop_column("store_product_variants", "allow_backorder")
