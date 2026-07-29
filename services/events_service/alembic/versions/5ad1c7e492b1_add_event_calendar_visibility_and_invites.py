"""add event calendar visibility and invites

Revision ID: 5ad1c7e492b1
Revises: e37ec62e2cff
Create Date: 2026-07-29
"""

import sqlalchemy as sa
from alembic import op


revision = "5ad1c7e492b1"
down_revision = "e37ec62e2cff"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "events",
        sa.Column("audience", sa.String(), server_default="community", nullable=False),
    )
    op.add_column(
        "events",
        sa.Column("visibility", sa.String(), server_default="public", nullable=False),
    )
    op.add_column(
        "events",
        sa.Column("status", sa.String(), server_default="published", nullable=False),
    )
    op.add_column(
        "events",
        sa.Column(
            "location_type", sa.String(), server_default="physical", nullable=False
        ),
    )
    op.add_column(
        "events",
        sa.Column(
            "timezone", sa.String(), server_default="Africa/Lagos", nullable=False
        ),
    )
    op.add_column("events", sa.Column("location_area", sa.String(), nullable=True))
    op.add_column(
        "events",
        sa.Column(
            "is_location_private",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )
    op.execute(
        """
        UPDATE events
        SET audience = CASE
            WHEN tier_access IN ('club', 'academy') THEN tier_access
            ELSE 'community'
        END,
        visibility = CASE
            WHEN tier_access IN ('club', 'academy') THEN 'members_only'
            ELSE 'public'
        END
        """
    )

    op.create_table(
        "event_invites",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("event_id", sa.UUID(), nullable=False),
        sa.Column("member_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "event_id", "member_id", name="uq_event_invites_event_member"
        ),
    )
    op.create_index(
        op.f("ix_event_invites_event_id"),
        "event_invites",
        ["event_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_event_invites_member_id"),
        "event_invites",
        ["member_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_event_invites_member_id"), table_name="event_invites")
    op.drop_index(op.f("ix_event_invites_event_id"), table_name="event_invites")
    op.drop_table("event_invites")
    op.drop_column("events", "is_location_private")
    op.drop_column("events", "location_area")
    op.drop_column("events", "timezone")
    op.drop_column("events", "location_type")
    op.drop_column("events", "status")
    op.drop_column("events", "visibility")
    op.drop_column("events", "audience")
