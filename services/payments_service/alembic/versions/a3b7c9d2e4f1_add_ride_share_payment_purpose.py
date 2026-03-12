"""add ride_share payment purpose

Revision ID: a3b7c9d2e4f1
Revises: d1e2f3a4b701
Create Date: 2026-03-12 15:00:00.000000
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "a3b7c9d2e4f1"
down_revision = "d1e2f3a4b701"
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
                  AND e.enumlabel = 'ride_share'
            ) THEN
                ALTER TYPE payment_purpose_enum ADD VALUE 'ride_share';
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    # PostgreSQL does not support removing enum values safely in-place.
    pass
