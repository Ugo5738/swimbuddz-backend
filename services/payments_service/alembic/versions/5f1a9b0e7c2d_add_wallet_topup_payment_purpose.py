"""add wallet topup payment purpose

Revision ID: 5f1a9b0e7c2d
Revises: e9b95f04e6cf
Create Date: 2026-02-18 03:12:00.000000
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "5f1a9b0e7c2d"
down_revision = "e9b95f04e6cf"
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
                  AND e.enumlabel = 'WALLET_TOPUP'
            ) THEN
                ALTER TYPE payment_purpose_enum ADD VALUE 'WALLET_TOPUP';
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    # PostgreSQL does not support removing enum values safely in-place.
    pass
