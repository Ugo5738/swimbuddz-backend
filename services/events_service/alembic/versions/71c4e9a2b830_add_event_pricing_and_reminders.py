"""add event pricing snapshots and reminder schedules

Revision ID: 71c4e9a2b830
Revises: 6f1a8b3c4d20
Create Date: 2026-08-10
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "71c4e9a2b830"
down_revision = "6f1a8b3c4d20"
branch_labels = None
depends_on = None


def _add_pricing_columns(table_name: str) -> None:
    op.add_column(
        table_name,
        sa.Column("pricing_mode", sa.String(length=24), server_default="fixed", nullable=False),
    )
    op.add_column(table_name, sa.Column("pricing_expected_attendees", sa.Integer(), nullable=True))
    op.add_column(
        table_name,
        sa.Column("cost_lines", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        table_name,
        sa.Column("estimated_total_cost", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        table_name,
        sa.Column(
            "estimated_cost_per_attendee", sa.Integer(), server_default="0", nullable=False
        ),
    )
    op.add_column(
        table_name,
        sa.Column(
            "margin_type",
            sa.String(length=32),
            server_default="fixed_per_attendee",
            nullable=False,
        ),
    )
    op.add_column(
        table_name,
        sa.Column("margin_value", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        table_name,
        sa.Column(
            "margin_amount_per_attendee", sa.Integer(), server_default="0", nullable=False
        ),
    )
    op.add_column(
        table_name,
        sa.Column(
            "email_reminder_hours",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def _drop_pricing_columns(table_name: str) -> None:
    op.drop_column(table_name, "email_reminder_hours")
    op.drop_column(table_name, "margin_amount_per_attendee")
    op.drop_column(table_name, "margin_value")
    op.drop_column(table_name, "margin_type")
    op.drop_column(table_name, "estimated_cost_per_attendee")
    op.drop_column(table_name, "estimated_total_cost")
    op.drop_column(table_name, "cost_lines")
    op.drop_column(table_name, "pricing_expected_attendees")
    op.drop_column(table_name, "pricing_mode")


def upgrade() -> None:
    _add_pricing_columns("events")
    _add_pricing_columns("event_templates")

    op.execute(
        """
        UPDATE events
        SET pricing_mode = CASE
            WHEN cost_kobo IS NULL OR cost_kobo = 0 THEN 'free'
            ELSE 'fixed'
        END
        """
    )
    op.execute(
        """
        UPDATE event_templates
        SET pricing_mode = CASE
            WHEN cost_kobo IS NULL OR cost_kobo = 0 THEN 'free'
            ELSE 'fixed'
        END
        """
    )
    op.execute(
        "UPDATE events SET email_reminder_hours = '[168,24,1]'::jsonb "
        "WHERE event_type = 'online_talk' AND status <> 'cancelled'"
    )
    op.execute(
        "UPDATE event_templates SET email_reminder_hours = '[168,24,1]'::jsonb "
        "WHERE event_type = 'online_talk'"
    )
    op.execute(
        "UPDATE events SET title = replace(replace(title, 'SwimBuddz Water Room', "
        "'Beyond the Pool'), 'Water Room', 'Beyond the Pool') "
        "WHERE title LIKE '%Water Room%'"
    )
    op.execute(
        "UPDATE event_templates SET title = replace(replace(title, 'SwimBuddz Water Room', "
        "'Beyond the Pool'), 'Water Room', 'Beyond the Pool') "
        "WHERE title LIKE '%Water Room%'"
    )

    op.create_table(
        "event_reminder_logs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("event_id", sa.UUID(), nullable=False),
        sa.Column("member_id", sa.UUID(), nullable=False),
        sa.Column("reminder_hours", sa.Integer(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "event_id",
            "member_id",
            "reminder_hours",
            name="uq_event_reminder_event_member_offset",
        ),
    )
    op.create_index(
        op.f("ix_event_reminder_logs_event_id"),
        "event_reminder_logs",
        ["event_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_event_reminder_logs_member_id"),
        "event_reminder_logs",
        ["member_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_event_reminder_logs_member_id"), table_name="event_reminder_logs")
    op.drop_index(op.f("ix_event_reminder_logs_event_id"), table_name="event_reminder_logs")
    op.drop_table("event_reminder_logs")
    _drop_pricing_columns("event_templates")
    _drop_pricing_columns("events")
