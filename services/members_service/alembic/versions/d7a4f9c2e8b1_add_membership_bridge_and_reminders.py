"""add membership bridge and reminder state

Revision ID: d7a4f9c2e8b1
Revises: c9a1e6f24b70
Create Date: 2026-07-22
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d7a4f9c2e8b1"
down_revision: Union[str, None] = "c9a1e6f24b70"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "member_memberships",
        sa.Column(
            "declared_tiers",
            postgresql.ARRAY(sa.String()),
            server_default=sa.text("ARRAY['community']::varchar[]"),
            nullable=False,
        ),
    )
    op.execute(
        """
        UPDATE member_memberships AS membership
        SET declared_tiers = ARRAY(
            SELECT DISTINCT tier
            FROM unnest(
                COALESCE(membership.active_tiers, ARRAY[]::varchar[])
                || ARRAY[LOWER(COALESCE(membership.primary_tier, ''))::varchar]::varchar[]
                || ARRAY['community']::varchar[]
                || CASE WHEN membership.club_paid_until IS NOT NULL
                        THEN ARRAY['club'] ELSE ARRAY[]::varchar[] END
                || CASE WHEN membership.academy_paid_until IS NOT NULL
                        THEN ARRAY['academy'] ELSE ARRAY[]::varchar[] END
            ) AS tier
            WHERE tier IN ('community', 'club', 'academy')
        )
        """
    )
    op.add_column(
        "member_memberships",
        sa.Column("post_academy_club_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "member_memberships",
        sa.Column("club_billing_cycle_months", sa.Integer(), nullable=True),
    )
    op.add_column(
        "member_memberships",
        sa.Column(
            "renewal_reminders_sent",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("member_memberships", "renewal_reminders_sent")
    op.drop_column("member_memberships", "club_billing_cycle_months")
    op.drop_column("member_memberships", "post_academy_club_until")
    op.drop_column("member_memberships", "declared_tiers")
