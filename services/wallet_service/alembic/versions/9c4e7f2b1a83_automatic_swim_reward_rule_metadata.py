"""Update verified_swim_day rule metadata for automatic post-session rewards.

Hand-written migration — raw SQL data migration updating existing reward_rules row
to match ADR-019 automatic crediting (requires_admin_confirmation = false) and
ADR-020 plain-English member display text.

Revision ID: 9c4e7f2b1a83
Revises: 27c30c24e91c
Create Date: 2026-11-23 22:15:00.000000
"""

from alembic import op

revision = "9c4e7f2b1a83"
down_revision = "27c30c24e91c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE reward_rules
        SET
            display_name = 'Daily Swim Reward',
            description = 'Earn 1 Bubble automatically after each eligible Club, Academy, or organised community swim day ends, up to 3 days per Lagos week.',
            requires_admin_confirmation = false,
            updated_at = now()
        WHERE rule_name = 'verified_swim_day'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE reward_rules
        SET
            display_name = 'Verified swim day',
            description = 'Earn one Bubble per confirmed swim day, up to three per Lagos week.',
            requires_admin_confirmation = true,
            updated_at = now()
        WHERE rule_name = 'verified_swim_day'
        """
    )
