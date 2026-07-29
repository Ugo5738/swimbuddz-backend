"""add extension coach payout billable flag

Revision ID: c8d4e6f7a901
Revises: 7eb7d65a0bba
Create Date: 2026-07-29 09:00:00
"""

import sqlalchemy as sa
from alembic import op


revision = "c8d4e6f7a901"
down_revision = "7eb7d65a0bba"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "cohort_extension_requests",
        sa.Column(
            "coach_payout_billable",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column(
        "cohort_extension_requests",
        sa.Column(
            "coach_payout_synced_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("cohort_extension_requests", "coach_payout_synced_at")
    op.drop_column("cohort_extension_requests", "coach_payout_billable")
