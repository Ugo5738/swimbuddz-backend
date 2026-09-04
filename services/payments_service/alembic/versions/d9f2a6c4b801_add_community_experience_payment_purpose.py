"""add Community Experience payment purpose

Revision ID: d9f2a6c4b801
Revises: c8e5f1a7d320
Create Date: 2026-08-13
"""

from alembic import op

revision = "d9f2a6c4b801"
down_revision = "c8e5f1a7d320"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TYPE payment_purpose_enum ADD VALUE IF NOT EXISTS 'community_experience'"
    )


def downgrade() -> None:
    # PostgreSQL enum values cannot be removed safely in-place.
    pass
