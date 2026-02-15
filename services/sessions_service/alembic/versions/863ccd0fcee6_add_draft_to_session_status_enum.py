"""add_draft_to_session_status_enum

Revision ID: 863ccd0fcee6
Revises: 66a7cc16dc78
Create Date: 2026-02-14 05:27:52.834600
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '863ccd0fcee6'
down_revision = '66a7cc16dc78'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add DRAFT to session_status_enum if it doesn't already exist.
    # Prod DB already has this value; dev DB does not.
    # The IF NOT EXISTS check makes this migration safe to run on both.
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_enum
                WHERE enumlabel = 'DRAFT'
                AND enumtypid = (SELECT oid FROM pg_type WHERE typname = 'session_status_enum')
            ) THEN
                ALTER TYPE session_status_enum ADD VALUE 'DRAFT' BEFORE 'SCHEDULED';
            END IF;
        END$$;
    """)


def downgrade() -> None:
    # PostgreSQL does not support removing values from an enum type.
    # To fully reverse this, you'd need to recreate the enum without DRAFT
    # and update all columns using it — which is destructive and rarely needed.
    pass
