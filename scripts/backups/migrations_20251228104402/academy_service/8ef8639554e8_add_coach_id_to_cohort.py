"""add_coach_id_to_cohort

Revision ID: 8ef8639554e8
Revises: 8cd2d2890ce0
Create Date: 2025-12-09 12:38:08.598078
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "8ef8639554e8"
down_revision = "8cd2d2890ce0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("cohorts", sa.Column("coach_id", sa.UUID(), nullable=True))


def downgrade() -> None:
    op.drop_column("cohorts", "coach_id")
