"""add weekly digest config and dispatch state

Revision ID: d912a6f40b77
Revises: b7d4e2a19c63
Create Date: 2026-07-17
"""

from datetime import datetime, timezone
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d912a6f40b77"
down_revision: Union[str, None] = "b7d4e2a19c63"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Missing preference rows are treated as opted in. Preserve that behavior
    # when a member first opens settings and materializes a row. Existing false
    # values remain explicit opt-outs.
    op.alter_column(
        "notification_preferences",
        "weekly_session_digest",
        server_default=sa.true(),
        existing_type=sa.Boolean(),
        existing_nullable=False,
    )

    now = datetime.now(timezone.utc)
    op.create_table(
        "weekly_digest_configs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("audience", sa.String(length=20), nullable=False),
        sa.Column("featured_image_media_id", postgresql.UUID(as_uuid=True)),
        sa.Column("image_alt", sa.String(length=240), nullable=False),
        sa.Column("section_intro", sa.Text()),
        sa.Column("default_gear_notes", sa.Text()),
        sa.Column("is_enabled", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("audience", name="uq_weekly_digest_configs_audience"),
    )
    op.create_table(
        "weekly_digest_dispatches",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("campaign_key", sa.String(length=40), nullable=False),
        sa.Column("member_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("recipient_email", sa.String(), nullable=False),
        sa.Column("tracking_token", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "delivery_status",
            sa.String(length=20),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_message", sa.Text()),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("click_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("first_clicked_at", sa.DateTime(timezone=True)),
        sa.Column("last_clicked_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tracking_token"),
        sa.UniqueConstraint(
            "campaign_key",
            "member_id",
            name="uq_weekly_digest_dispatches_campaign_member",
        ),
    )
    op.create_index(
        "ix_weekly_digest_dispatches_campaign_key",
        "weekly_digest_dispatches",
        ["campaign_key"],
    )
    op.create_index(
        "ix_weekly_digest_dispatches_member_id",
        "weekly_digest_dispatches",
        ["member_id"],
    )
    op.bulk_insert(
        sa.table(
            "weekly_digest_configs",
            sa.column("id", postgresql.UUID(as_uuid=True)),
            sa.column("audience", sa.String()),
            sa.column("image_alt", sa.String()),
            sa.column("section_intro", sa.Text()),
            sa.column("default_gear_notes", sa.Text()),
            sa.column("is_enabled", sa.Boolean()),
            sa.column("created_at", sa.DateTime(timezone=True)),
            sa.column("updated_at", sa.DateTime(timezone=True)),
        ),
        [
            {
                "id": "0d2a45fa-bffb-4a31-9fe7-692d601c0c01",
                "audience": "community",
                "image_alt": "SwimBuddz Community members swimming together",
                "section_intro": "Social swims open to eligible Community members.",
                "default_gear_notes": "Swimsuit, goggles, cap, towel, and water.",
                "is_enabled": True,
                "created_at": now,
                "updated_at": now,
            },
            {
                "id": "0d2a45fa-bffb-4a31-9fe7-692d601c0c02",
                "audience": "club",
                "image_alt": "SwimBuddz Club members practising in lanes",
                "section_intro": "Your pod practice and general Club sessions.",
                "default_gear_notes": "Bring your usual swim kit and any training aids listed by your Pod Lead.",
                "is_enabled": True,
                "created_at": now,
                "updated_at": now,
            },
            {
                "id": "0d2a45fa-bffb-4a31-9fe7-692d601c0c03",
                "audience": "academy",
                "image_alt": "SwimBuddz Academy students in a coached lesson",
                "section_intro": "Your cohort lessons and this week's learning focus.",
                "default_gear_notes": "Swimsuit, goggles, cap, towel, water, and any coach-assigned training aids.",
                "is_enabled": True,
                "created_at": now,
                "updated_at": now,
            },
        ],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_weekly_digest_dispatches_member_id",
        table_name="weekly_digest_dispatches",
    )
    op.drop_index(
        "ix_weekly_digest_dispatches_campaign_key",
        table_name="weekly_digest_dispatches",
    )
    op.drop_table("weekly_digest_dispatches")
    op.drop_table("weekly_digest_configs")
    op.alter_column(
        "notification_preferences",
        "weekly_session_digest",
        server_default=None,
        existing_type=sa.Boolean(),
        existing_nullable=False,
    )
