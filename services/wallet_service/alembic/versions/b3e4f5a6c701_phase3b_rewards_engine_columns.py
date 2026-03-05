"""phase3b_rewards_engine_columns

Revision ID: b3e4f5a6c701
Revises: 1cc37d30b0f1
Create Date: 2026-02-27 06:00:00.000000

Hand-written migration — Alembic autogenerate would interpret column renames
as DROP+ADD, causing data loss. This migration safely renames columns, adds
new columns, creates new enum types, and adds indexes/constraints.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "b3e4f5a6c701"
down_revision = "1cc37d30b0f1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---- 1. Create new enum types ----
    reward_category_enum = sa.Enum(
        "acquisition", "retention", "community", "spending", "academy",
        name="reward_category_enum",
    )
    reward_period_enum = sa.Enum(
        "day", "week", "month", "year",
        name="reward_period_enum",
    )
    reward_category_enum.create(op.get_bind(), checkfirst=True)
    reward_period_enum.create(op.get_bind(), checkfirst=True)

    # ---- 2. reward_rules: rename columns ----
    op.alter_column("reward_rules", "name", new_column_name="rule_name")
    op.alter_column("reward_rules", "condition_config", new_column_name="trigger_config")
    op.alter_column("reward_rules", "max_grants_per_member", new_column_name="max_per_member_lifetime")
    op.alter_column("reward_rules", "max_grants_per_period", new_column_name="max_per_member_per_period")

    # ---- 3. reward_rules: drop period_days, add period enum ----
    op.drop_column("reward_rules", "period_days")
    op.add_column(
        "reward_rules",
        sa.Column(
            "period",
            sa.Enum("day", "week", "month", "year", name="reward_period_enum"),
            nullable=True,
        ),
    )

    # ---- 4. reward_rules: add new columns ----
    op.add_column(
        "reward_rules",
        sa.Column("display_name", sa.String(), nullable=False, server_default=""),
    )
    op.add_column(
        "reward_rules",
        sa.Column("reward_description_template", sa.Text(), nullable=True),
    )
    op.add_column(
        "reward_rules",
        sa.Column("replaces_rule_id", sa.UUID(), nullable=True),
    )
    op.add_column(
        "reward_rules",
        sa.Column(
            "category",
            sa.Enum("acquisition", "retention", "community", "spending", "academy", name="reward_category_enum"),
            nullable=False,
            server_default="retention",
        ),
    )
    op.add_column(
        "reward_rules",
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "reward_rules",
        sa.Column("requires_admin_confirmation", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "reward_rules",
        sa.Column("created_by", sa.String(), nullable=True),
    )

    # Remove server_defaults that were only needed for existing rows
    op.alter_column("reward_rules", "display_name", server_default=None)
    op.alter_column("reward_rules", "category", server_default=None)
    op.alter_column("reward_rules", "priority", server_default=None)
    op.alter_column("reward_rules", "requires_admin_confirmation", server_default=None)

    # ---- 5. reward_rules: add unique constraint + composite index + check constraint ----
    op.create_unique_constraint("uq_reward_rules_rule_name", "reward_rules", ["rule_name"])
    op.create_index(
        "ix_reward_rules_event_type_active", "reward_rules", ["event_type", "is_active"]
    )
    op.execute(
        "ALTER TABLE reward_rules ADD CONSTRAINT ck_reward_rules_positive_bubbles "
        "CHECK (reward_bubbles > 0)"
    )

    # ---- 6. wallet_events: rename column ----
    op.alter_column("wallet_events", "source_service", new_column_name="service_source")

    # ---- 7. wallet_events: add new columns ----
    op.add_column(
        "wallet_events",
        sa.Column("event_id", sa.UUID(), nullable=True),
    )
    op.add_column(
        "wallet_events",
        sa.Column("member_id", sa.UUID(), nullable=True),
    )
    op.add_column(
        "wallet_events",
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "wallet_events",
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "wallet_events",
        sa.Column("rewards_granted", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "wallet_events",
        sa.Column("processing_error", sa.Text(), nullable=True),
    )

    # Backfill event_id with the row id for existing rows, then make NOT NULL + unique
    op.execute("UPDATE wallet_events SET event_id = id WHERE event_id IS NULL")
    op.alter_column("wallet_events", "event_id", nullable=False)
    op.create_unique_constraint("uq_wallet_events_event_id", "wallet_events", ["event_id"])
    op.create_index("ix_wallet_events_event_id", "wallet_events", ["event_id"], unique=True)

    # Backfill occurred_at/received_at from created_at, then make NOT NULL
    op.execute("UPDATE wallet_events SET occurred_at = created_at WHERE occurred_at IS NULL")
    op.execute("UPDATE wallet_events SET received_at = created_at WHERE received_at IS NULL")
    op.alter_column("wallet_events", "occurred_at", nullable=False)
    op.alter_column("wallet_events", "received_at", nullable=False)

    # Remove server_default for rewards_granted
    op.alter_column("wallet_events", "rewards_granted", server_default=None)

    # Composite index for retry queue
    op.create_index(
        "ix_wallet_events_processed_created", "wallet_events", ["processed", "created_at"]
    )

    # ---- 8. member_reward_history: add period_key + composite index ----
    op.add_column(
        "member_reward_history",
        sa.Column("period_key", sa.String(), nullable=True),
    )
    op.create_index(
        "ix_member_reward_history_cap_check",
        "member_reward_history",
        ["member_auth_id", "reward_rule_id", "period_key"],
    )


def downgrade() -> None:
    # ---- member_reward_history ----
    op.drop_index("ix_member_reward_history_cap_check", table_name="member_reward_history")
    op.drop_column("member_reward_history", "period_key")

    # ---- wallet_events ----
    op.drop_index("ix_wallet_events_processed_created", table_name="wallet_events")
    op.drop_index("ix_wallet_events_event_id", table_name="wallet_events")
    op.drop_constraint("uq_wallet_events_event_id", "wallet_events", type_="unique")
    op.drop_column("wallet_events", "processing_error")
    op.drop_column("wallet_events", "rewards_granted")
    op.drop_column("wallet_events", "received_at")
    op.drop_column("wallet_events", "occurred_at")
    op.drop_column("wallet_events", "member_id")
    op.drop_column("wallet_events", "event_id")
    op.alter_column("wallet_events", "service_source", new_column_name="source_service")

    # ---- reward_rules ----
    op.execute("ALTER TABLE reward_rules DROP CONSTRAINT IF EXISTS ck_reward_rules_positive_bubbles")
    op.drop_index("ix_reward_rules_event_type_active", table_name="reward_rules")
    op.drop_constraint("uq_reward_rules_rule_name", "reward_rules", type_="unique")
    op.drop_column("reward_rules", "created_by")
    op.drop_column("reward_rules", "requires_admin_confirmation")
    op.drop_column("reward_rules", "priority")
    op.drop_column("reward_rules", "category")
    op.drop_column("reward_rules", "replaces_rule_id")
    op.drop_column("reward_rules", "reward_description_template")
    op.drop_column("reward_rules", "display_name")
    op.drop_column("reward_rules", "period")
    op.add_column("reward_rules", sa.Column("period_days", sa.Integer(), nullable=True))
    op.alter_column("reward_rules", "max_per_member_per_period", new_column_name="max_grants_per_period")
    op.alter_column("reward_rules", "max_per_member_lifetime", new_column_name="max_grants_per_member")
    op.alter_column("reward_rules", "trigger_config", new_column_name="condition_config")
    op.alter_column("reward_rules", "rule_name", new_column_name="name")

    # ---- Drop enum types ----
    sa.Enum(name="reward_period_enum").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="reward_category_enum").drop(op.get_bind(), checkfirst=True)
