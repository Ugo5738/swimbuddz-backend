"""add guest-pass payment purpose

Revision ID: c8e5f1a7d320
Revises: b7d3e9a4c210
Create Date: 2026-08-11
"""

from alembic import op

revision = "c8e5f1a7d320"
down_revision = "b7d3e9a4c210"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE payment_purpose_enum ADD VALUE IF NOT EXISTS 'guest_pass'")


def downgrade() -> None:
    # PostgreSQL enum values cannot be removed safely in-place.
    pass
