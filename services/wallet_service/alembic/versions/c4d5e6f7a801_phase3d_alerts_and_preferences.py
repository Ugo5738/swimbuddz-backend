"""Phase 3d — reward alerts and notification preferences tables.

Revision ID: c4d5e6f7a801
Revises: b3e4f5a6c701
Create Date: 2026-02-27

New tables:
- reward_alerts: Anti-abuse monitoring alerts for the admin dashboard.
- reward_notification_preferences: Per-member notification settings for rewards.

New PG enum types:
- alert_severity_enum (low, medium, high, critical)
- alert_status_enum (open, acknowledged, resolved, dismissed)
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.dialects import postgresql

revision = "c4d5e6f7a801"
down_revision = "b3e4f5a6c701"
branch_labels = None
depends_on = None

# Enum types — create_type=False so create_table won't auto-create them;
# we handle creation/deletion explicitly in upgrade/downgrade.
alert_severity_enum = postgresql.ENUM(
    "low", "medium", "high", "critical",
    name="alert_severity_enum",
    create_type=False,
)
alert_status_enum = postgresql.ENUM(
    "open", "acknowledged", "resolved", "dismissed",
    name="alert_status_enum",
    create_type=False,
)


def upgrade() -> None:
    # --- Enum types ---
    alert_severity_enum.create(op.get_bind(), checkfirst=True)
    alert_status_enum.create(op.get_bind(), checkfirst=True)

    # --- reward_alerts ---
    op.create_table(
        "reward_alerts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("alert_type", sa.String(), nullable=False),
        sa.Column("severity", alert_severity_enum, nullable=False),
        sa.Column("status", alert_status_enum, nullable=False, server_default="open"),
        sa.Column("member_auth_id", sa.String(), nullable=True),
        sa.Column("referral_code_id", UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("alert_data", JSONB(), nullable=False, server_default="{}"),
        sa.Column("resolved_by", sa.String(), nullable=True),
        sa.Column(
            "resolved_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("resolution_notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_reward_alerts_alert_type", "reward_alerts", ["alert_type"])
    op.create_index("ix_reward_alerts_member_auth_id", "reward_alerts", ["member_auth_id"])
    op.create_index(
        "ix_reward_alerts_status_created",
        "reward_alerts",
        ["status", "created_at"],
    )

    # --- reward_notification_preferences ---
    op.create_table(
        "reward_notification_preferences",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("member_auth_id", sa.String(), nullable=False),
        sa.Column(
            "notify_on_reward",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "notify_on_referral_qualified",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "notify_on_ambassador_milestone",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "notify_on_streak_milestone",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "notify_channel",
            sa.String(),
            nullable=False,
            server_default="in_app",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_reward_notif_prefs_member_auth_id",
        "reward_notification_preferences",
        ["member_auth_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("reward_notification_preferences")
    op.drop_table("reward_alerts")
    alert_status_enum.drop(op.get_bind(), checkfirst=True)
    alert_severity_enum.drop(op.get_bind(), checkfirst=True)
