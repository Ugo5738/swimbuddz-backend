"""Pool Access is a first-class payment purpose.

Revision ID: pa20261008b1
Revises: 36dbbd8ca925, d9f2a6c4b801
"""
from alembic import op

revision = "pa20261008b1"
down_revision = ("36dbbd8ca925", "d9f2a6c4b801")
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE payment_purpose_enum ADD VALUE IF NOT EXISTS 'pool_access'")


def downgrade() -> None:
    # Removing a PostgreSQL enum value safely requires a data migration.
    pass
