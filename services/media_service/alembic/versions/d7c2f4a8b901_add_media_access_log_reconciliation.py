"""Add billing-grade media access-log reconciliation.

Revision ID: d7c2f4a8b901
Revises: c0a4d1f91e21
Create Date: 2026-08-02
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d7c2f4a8b901"
down_revision: Union[str, None] = "c0a4d1f91e21"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "media_transfer_logs",
        sa.Column("object_key", sa.String(length=1024), nullable=True),
    )
    op.create_index(
        "ix_media_transfer_logs_object_key",
        "media_transfer_logs",
        ["object_key"],
        unique=False,
    )

    op.create_table(
        "media_access_log_objects",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "provider", sa.String(length=24), server_default="s3", nullable=False
        ),
        sa.Column("source_bucket", sa.String(length=255), nullable=False),
        sa.Column("object_key", sa.String(length=1024), nullable=False),
        sa.Column("etag", sa.String(length=128), nullable=True),
        sa.Column(
            "status", sa.String(length=24), server_default="processing", nullable=False
        ),
        sa.Column("event_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "matched_event_count", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider",
            "source_bucket",
            "object_key",
            name="uq_media_access_log_object_source",
        ),
    )
    op.create_index(
        "ix_media_access_log_objects_status",
        "media_access_log_objects",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_media_access_log_objects_processed_at",
        "media_access_log_objects",
        ["processed_at"],
        unique=False,
    )

    op.create_table(
        "media_access_log_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "source_log_object_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("transfer_log_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "provider", sa.String(length=24), server_default="s3", nullable=False
        ),
        sa.Column("target_bucket", sa.String(length=255), nullable=False),
        sa.Column("request_id", sa.String(length=160), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("object_key", sa.String(length=1024), nullable=False),
        sa.Column("bytes_sent", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column(
            "match_status",
            sa.String(length=24),
            server_default="unmatched",
            nullable=False,
        ),
        sa.Column("remote_ip", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_log_object_id"],
            ["media_access_log_objects.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["transfer_log_id"], ["media_transfer_logs.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider",
            "target_bucket",
            "request_id",
            name="uq_media_access_log_event_request",
        ),
    )
    for column_name in (
        "source_log_object_id",
        "transfer_log_id",
        "occurred_at",
        "object_key",
        "match_status",
    ):
        op.create_index(
            f"ix_media_access_log_events_{column_name}",
            "media_access_log_events",
            [column_name],
            unique=False,
        )
    op.create_index(
        "ix_media_access_log_events_occurred_match",
        "media_access_log_events",
        ["occurred_at", "match_status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("media_access_log_events")
    op.drop_table("media_access_log_objects")
    op.drop_index("ix_media_transfer_logs_object_key", table_name="media_transfer_logs")
    op.drop_column("media_transfer_logs", "object_key")
