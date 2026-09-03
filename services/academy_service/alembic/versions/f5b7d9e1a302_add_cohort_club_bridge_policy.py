"""add cohort Club bridge policy

Revision ID: f5b7d9e1a302
Revises: f4a6c8e0b201
Create Date: 2026-09-03

Hand-written migration — includes a constraint that Alembic autogenerate cannot
safely express.
"""

import sqlalchemy as sa
from alembic import op

revision = "f5b7d9e1a302"
down_revision = "f4a6c8e0b201"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "cohorts",
        sa.Column("post_graduation_club_bridge_months", sa.Integer(), nullable=True),
    )
    op.create_check_constraint(
        "ck_cohorts_post_graduation_club_bridge_months",
        "cohorts",
        "post_graduation_club_bridge_months IS NULL OR "
        "(post_graduation_club_bridge_months >= 0 AND "
        "post_graduation_club_bridge_months <= 12)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_cohorts_post_graduation_club_bridge_months",
        "cohorts",
        type_="check",
    )
    op.drop_column("cohorts", "post_graduation_club_bridge_months")
