"""Snapshot the Academy enrollment's Membership policy.

Revision ID: f6c8e0a2b413
Revises: f5b7d9e1a302
Create Date: 2026-09-06

Historical policy cannot be reconstructed reliably. Leave existing enrollments
null so they use the documented current-policy fallback; do not backfill a
current policy and misrepresent it as their original agreement.
"""

import sqlalchemy as sa
from alembic import op

revision = "f6c8e0a2b413"
down_revision = "f5b7d9e1a302"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "enrollments",
        sa.Column("membership_policy_snapshot", sa.String(length=24), nullable=True),
    )
    op.create_check_constraint(
        "ck_enrollments_membership_policy_snapshot",
        "enrollments",
        "membership_policy_snapshot IS NULL OR membership_policy_snapshot IN "
        "('open', 'active_required', 'included')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_enrollments_membership_policy_snapshot", "enrollments", type_="check"
    )
    op.drop_column("enrollments", "membership_policy_snapshot")
