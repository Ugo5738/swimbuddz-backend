"""add session_bundle payment purpose

Revision ID: b4c8d2e5f3a1
Revises: a3b7c9d2e4f1
Create Date: 2026-04-01 20:00:00.000000
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "b4c8d2e5f3a1"
down_revision = "a3b7c9d2e4f1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_enum e
                JOIN pg_type t ON t.oid = e.enumtypid
                WHERE t.typname = 'payment_purpose_enum'
                  AND e.enumlabel = 'session_bundle'
            ) THEN
                ALTER TYPE payment_purpose_enum ADD VALUE 'session_bundle';
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    # PostgreSQL does not support removing enum values safely in-place.
    pass
