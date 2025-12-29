"""add club activation purpose

Revision ID: 3806fe97fbd7
Revises: 0a72aa81d384
Create Date: 2025-12-23 07:33:52.575088
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "3806fe97fbd7"
down_revision = "0a72aa81d384"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TYPE payment_purpose_enum ADD VALUE IF NOT EXISTS 'CLUB_ACTIVATION'"
    )


def downgrade() -> None:
    # Removing enum values in Postgres is unsafe without type recreation.
    pass
