"""add strokelab founding payment purpose

Revision ID: 94441010d50c
Revises: 7002342f75af
Create Date: 2026-06-09 23:06:55.464375
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = '94441010d50c'
down_revision = '7002342f75af'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add the lowercase enum value to match the model's values_callable
    # (the recent additions ride_share / session_bundle / session_booking
    # are lowercase-only — follow that convention, NOT the legacy dual-case
    # labels). Guarded so a re-run is a no-op.
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_enum e
                JOIN pg_type t ON t.oid = e.enumtypid
                WHERE t.typname = 'payment_purpose_enum'
                  AND e.enumlabel = 'strokelab_founding'
            ) THEN
                ALTER TYPE payment_purpose_enum ADD VALUE 'strokelab_founding';
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    # PostgreSQL does not support removing enum values safely in-place.
    pass
