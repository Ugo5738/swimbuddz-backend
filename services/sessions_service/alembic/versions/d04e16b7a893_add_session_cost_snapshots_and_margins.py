"""add session cost snapshots and margins

Revision ID: d04e16b7a893
Revises: a015127750f3
Create Date: 2026-07-29
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "d04e16b7a893"
down_revision = "a015127750f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sessions",
        sa.Column(
            "pricing_mode", sa.String(length=24), server_default="manual", nullable=False
        ),
    )
    op.add_column(
        "sessions",
        sa.Column("pricing_expected_attendees", sa.Integer(), nullable=True),
    )
    op.add_column(
        "sessions",
        sa.Column("cost_lines", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "sessions",
        sa.Column("estimated_total_cost", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "sessions",
        sa.Column(
            "estimated_cost_per_attendee",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    op.add_column(
        "sessions",
        sa.Column(
            "margin_type",
            sa.String(length=32),
            server_default="fixed_per_attendee",
            nullable=False,
        ),
    )
    op.add_column(
        "sessions",
        sa.Column("margin_value", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "sessions",
        sa.Column(
            "margin_amount_per_attendee",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("sessions", "margin_amount_per_attendee")
    op.drop_column("sessions", "margin_value")
    op.drop_column("sessions", "margin_type")
    op.drop_column("sessions", "estimated_cost_per_attendee")
    op.drop_column("sessions", "estimated_total_cost")
    op.drop_column("sessions", "cost_lines")
    op.drop_column("sessions", "pricing_expected_attendees")
    op.drop_column("sessions", "pricing_mode")
