"""add_price_to_program

Revision ID: 7cc71d2490eb
Revises: 8ef8639554e8
Create Date: 2025-12-09 12:40:38.341903
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '7cc71d2490eb'
down_revision = '8ef8639554e8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("programs", sa.Column("price", sa.Integer(), nullable=True, server_default="0"))


def downgrade() -> None:
    op.drop_column("programs", "price")
