"""reconcile_verified_swim_daily_rewards

Hand-written migration — includes the replacement of the legacy attendance offers.

Revision ID: 27c30c24e91c
Revises: a7b9d3e5f012
Create Date: 2026-11-19 22:40:28.310467
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "27c30c24e91c"
down_revision = "a7b9d3e5f012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "wallet_verified_swims",
        sa.Column(
            "wallet_id",
            sa.Uuid(),
            sa.ForeignKey("wallets.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("session_id", sa.Uuid(), primary_key=True),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("swim_date", sa.Date()),
        sa.Column("issued", sa.Integer(), nullable=False, server_default="0"),
    )
    op.execute("ALTER TABLE wallet_verified_swims ENABLE ROW LEVEL SECURITY")
    op.execute("""
        INSERT INTO reward_rules (id, rule_name, display_name, description, event_type, reward_bubbles,
            max_per_member_per_period, period, category, is_active, priority, requires_admin_confirmation, created_by, created_at, updated_at)
        VALUES ('6a7d9ae3-1c10-48ce-8f7a-149782b5089c','verified_swim_day','Verified swim day',
            'Earn one Bubble per confirmed swim day, up to three per Lagos week.',
            'attendance.verified_swim_day',1,3,'week','retention',true,0,true,'swim_rewards',now(),now())
    """)
    op.execute(
        "UPDATE reward_rules SET is_active=false WHERE rule_name IN ('monthly_attendance_4','monthly_attendance_8','attendance_streak_4_weeks')"
    )


def downgrade() -> None:
    op.execute("DELETE FROM reward_rules WHERE rule_name='verified_swim_day'")
    op.drop_table("wallet_verified_swims")
