"""merge content acquisition and quarterly reporting heads

Revision ID: 8cb71fcb8897
Revises: a08e16d7c9fa, d4e8f1a6b209
Create Date: 2026-10-11 10:13:37.659193
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '8cb71fcb8897'
down_revision = ('a08e16d7c9fa', 'd4e8f1a6b209')
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
