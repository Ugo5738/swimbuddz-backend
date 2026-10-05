"""add 10-Bubble volunteer of the month reward

Revision ID: a7b9d3e5f012
Revises: f6a8c2d4e901
Create Date: 2026-10-05
"""

from alembic import op


revision = "a7b9d3e5f012"
down_revision = "f6a8c2d4e901"
branch_labels = None
depends_on = None


RULE_ID = "00000000-0000-0000-0000-100000000023"


def upgrade() -> None:
    op.execute(
        f"""
        INSERT INTO reward_rules (
            id,
            rule_name,
            display_name,
            description,
            event_type,
            trigger_config,
            reward_bubbles,
            reward_description_template,
            max_per_member_lifetime,
            max_per_member_per_period,
            period,
            replaces_rule_id,
            category,
            is_active,
            priority,
            requires_admin_confirmation,
            created_by,
            created_at,
            updated_at
        )
        VALUES (
            '{RULE_ID}',
            'volunteer_of_the_month',
            'Volunteer of the Month',
            'Awards 10 Bubbles to each selected monthly volunteer; events deduplicate by member and display month.',
            'volunteer.monthly_spotlight',
            '{{}}'::jsonb,
            10,
            'Volunteer of the Month — {{month}} ({{amount}} 🫧)',
            NULL,
            NULL,
            NULL,
            NULL,
            'community',
            true,
            0,
            false,
            'migration',
            now(),
            now()
        )
        ON CONFLICT (rule_name) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute(
        f"DELETE FROM reward_rules WHERE id = '{RULE_ID}' "
        "AND rule_name = 'volunteer_of_the_month'"
    )
