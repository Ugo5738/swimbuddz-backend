"""add guest first-attendance referral reward rule

Revision ID: f6a8c2d4e901
Revises: e72d4c8a9130
Create Date: 2026-08-13
"""

from alembic import op


revision = "f6a8c2d4e901"
down_revision = "e72d4c8a9130"
branch_labels = None
depends_on = None


RULE_ID = "00000000-0000-0000-0000-100000000022"


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
            'guest_first_attendance_referral',
            'Guest Referral Thank-you',
            'Rewards the referrer once when a referred guest first attends a paid swim.',
            'referral.guest_attended',
            '{{}}'::jsonb,
            10,
            'Guest referral thank-you — {{guest_name}} attended ({{amount}} 🫧)',
            NULL,
            NULL,
            NULL,
            NULL,
            'acquisition',
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
        "AND rule_name = 'guest_first_attendance_referral'"
    )
