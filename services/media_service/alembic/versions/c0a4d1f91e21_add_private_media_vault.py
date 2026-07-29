"""Add private session media vault.

Revision ID: c0a4d1f91e21
Revises: 7b440e62d536
Create Date: 2026-07-29
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c0a4d1f91e21"
down_revision: Union[str, None] = "7b440e62d536"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "media_vaults",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("capture_date", sa.Date(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "timezone",
            sa.String(length=80),
            server_default="Africa/Lagos",
            nullable=False,
        ),
        sa.Column("location_name", sa.String(length=255), nullable=True),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default="scheduled",
            nullable=False,
        ),
        sa.Column("upload_opens_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("upload_closes_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "max_file_bytes",
            sa.BigInteger(),
            server_default=str(500 * 1024**3),
            nullable=False,
        ),
        sa.Column(
            "max_total_bytes",
            sa.BigInteger(),
            server_default=str(2 * 1024**4),
            nullable=False,
        ),
        sa.Column("used_bytes", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column(
            "auto_transcode", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
        sa.Column("retention_days", sa.Integer(), server_default="730", nullable=False),
        sa.Column("consent_notice", sa.Text(), nullable=True),
        sa.Column("opt_out_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "shot_checklist",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "settings_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("published_album_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", name="uq_media_vaults_event_id"),
        sa.UniqueConstraint("session_id", name="uq_media_vaults_session_id"),
    )
    op.create_index(
        "ix_media_vaults_capture_date",
        "media_vaults",
        ["capture_date"],
        unique=False,
    )
    op.create_index(
        "ix_media_vaults_capture_date_status",
        "media_vaults",
        ["capture_date", "status"],
        unique=False,
    )
    op.create_index(
        "ix_media_vaults_event_id", "media_vaults", ["event_id"], unique=False
    )
    op.create_index(
        "ix_media_vaults_session_id", "media_vaults", ["session_id"], unique=False
    )
    op.create_index("ix_media_vaults_status", "media_vaults", ["status"], unique=False)
    op.create_index(
        "ix_media_vaults_upload_closes_at",
        "media_vaults",
        ["upload_closes_at"],
        unique=False,
    )

    op.create_table(
        "media_vault_grants",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("vault_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("member_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(length=24), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "source", sa.String(length=32), server_default="manual", nullable=False
        ),
        sa.Column("source_reference_id", sa.String(length=255), nullable=True),
        sa.Column(
            "can_download_originals",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["vault_id"], ["media_vaults.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "vault_id", "member_id", "role", name="uq_media_vault_grant_role"
        ),
    )
    op.create_index(
        "ix_media_vault_grants_expires_at",
        "media_vault_grants",
        ["expires_at"],
        unique=False,
    )
    op.create_index(
        "ix_media_vault_grants_member_id",
        "media_vault_grants",
        ["member_id"],
        unique=False,
    )
    op.create_index(
        "ix_media_vault_grants_vault_id",
        "media_vault_grants",
        ["vault_id"],
        unique=False,
    )

    op.create_table(
        "media_vault_guest_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("vault_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=160), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "max_total_bytes",
            sa.BigInteger(),
            server_default=str(100 * 1024**3),
            nullable=False,
        ),
        sa.Column("used_bytes", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["vault_id"], ["media_vaults.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_media_vault_guest_links_expires_at",
        "media_vault_guest_links",
        ["expires_at"],
        unique=False,
    )
    op.create_index(
        "ix_media_vault_guest_links_token_hash",
        "media_vault_guest_links",
        ["token_hash"],
        unique=True,
    )
    op.create_index(
        "ix_media_vault_guest_links_vault_id",
        "media_vault_guest_links",
        ["vault_id"],
        unique=False,
    )

    op.create_table(
        "media_upload_batches",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("vault_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("uploader_member_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("uploader_auth_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("guest_link_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "status", sa.String(length=24), server_default="open", nullable=False
        ),
        sa.Column("expected_files", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "expected_bytes", sa.BigInteger(), server_default="0", nullable=False
        ),
        sa.Column("completed_files", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "completed_bytes", sa.BigInteger(), server_default="0", nullable=False
        ),
        sa.Column("consent_attested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consent_attestation_text", sa.Text(), nullable=False),
        sa.Column(
            "checklist_completed",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["guest_link_id"],
            ["media_vault_guest_links.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["vault_id"], ["media_vaults.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_media_upload_batches_status",
        "media_upload_batches",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_media_upload_batches_uploader_member_id",
        "media_upload_batches",
        ["uploader_member_id"],
        unique=False,
    )
    op.create_index(
        "ix_media_upload_batches_vault_id",
        "media_upload_batches",
        ["vault_id"],
        unique=False,
    )

    for name, column in (
        (
            "vault_id",
            sa.Column(
                "vault_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("media_vaults.id", ondelete="SET NULL"),
                nullable=True,
            ),
        ),
        (
            "upload_batch_id",
            sa.Column(
                "upload_batch_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("media_upload_batches.id", ondelete="SET NULL"),
                nullable=True,
            ),
        ),
        ("object_key", sa.Column("object_key", sa.String(1024), nullable=True)),
        ("bucket_type", sa.Column("bucket_type", sa.String(16), nullable=True)),
        (
            "original_filename",
            sa.Column("original_filename", sa.String(512), nullable=True),
        ),
        ("content_type", sa.Column("content_type", sa.String(255), nullable=True)),
        ("size_bytes", sa.Column("size_bytes", sa.BigInteger(), nullable=True)),
        (
            "client_fingerprint",
            sa.Column("client_fingerprint", sa.String(128), nullable=True),
        ),
        (
            "checksum_sha256",
            sa.Column("checksum_sha256", sa.String(64), nullable=True),
        ),
        (
            "multipart_upload_id",
            sa.Column("multipart_upload_id", sa.String(1024), nullable=True),
        ),
        (
            "captured_at",
            sa.Column("captured_at", sa.DateTime(timezone=True), nullable=True),
        ),
        (
            "processing_status",
            sa.Column(
                "processing_status",
                sa.String(24),
                server_default="ready",
                nullable=False,
            ),
        ),
        (
            "review_status",
            sa.Column(
                "review_status",
                sa.String(24),
                server_default="unreviewed",
                nullable=False,
            ),
        ),
        (
            "consent_status",
            sa.Column(
                "consent_status",
                sa.String(24),
                server_default="unreviewed",
                nullable=False,
            ),
        ),
        ("rating", sa.Column("rating", sa.Integer(), nullable=True)),
        ("review_notes", sa.Column("review_notes", sa.Text(), nullable=True)),
        (
            "rejection_reason",
            sa.Column("rejection_reason", sa.Text(), nullable=True),
        ),
        (
            "reviewed_by",
            sa.Column("reviewed_by", postgresql.UUID(as_uuid=True), nullable=True),
        ),
        (
            "reviewed_at",
            sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        ),
        ("proxy_url", sa.Column("proxy_url", sa.String(2048), nullable=True)),
        (
            "proxy_object_key",
            sa.Column("proxy_object_key", sa.String(1024), nullable=True),
        ),
        (
            "thumbnail_object_key",
            sa.Column("thumbnail_object_key", sa.String(1024), nullable=True),
        ),
        (
            "duplicate_of_id",
            sa.Column("duplicate_of_id", postgresql.UUID(as_uuid=True), nullable=True),
        ),
        (
            "published_media_id",
            sa.Column(
                "published_media_id",
                postgresql.UUID(as_uuid=True),
                nullable=True,
            ),
        ),
        (
            "published_at",
            sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        ),
        (
            "soft_deleted_at",
            sa.Column("soft_deleted_at", sa.DateTime(timezone=True), nullable=True),
        ),
    ):
        op.add_column("media_items", column)

    for column_name in (
        "vault_id",
        "upload_batch_id",
        "client_fingerprint",
        "checksum_sha256",
        "review_status",
        "consent_status",
        "soft_deleted_at",
    ):
        op.create_index(
            f"ix_media_items_{column_name}",
            "media_items",
            [column_name],
            unique=False,
        )

    op.create_table(
        "media_vault_exports",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("vault_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requested_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("format", sa.String(length=24), server_default="zip", nullable=False),
        sa.Column(
            "preset", sa.String(length=32), server_default="original", nullable=False
        ),
        sa.Column(
            "status", sa.String(length=24), server_default="pending", nullable=False
        ),
        sa.Column(
            "media_item_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("object_key", sa.String(length=1024), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["vault_id"], ["media_vaults.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    for column_name in ("vault_id", "requested_by", "status", "expires_at"):
        op.create_index(
            f"ix_media_vault_exports_{column_name}",
            "media_vault_exports",
            [column_name],
            unique=False,
        )

    op.create_table(
        "media_transfer_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("vault_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("media_item_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("export_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_member_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("transfer_type", sa.String(length=24), nullable=False),
        sa.Column("direction", sa.String(length=8), nullable=False),
        sa.Column("delivery_method", sa.String(length=32), nullable=False),
        sa.Column(
            "status", sa.String(length=24), server_default="authorized", nullable=False
        ),
        sa.Column(
            "bytes_authorized", sa.BigInteger(), server_default="0", nullable=False
        ),
        sa.Column(
            "bytes_transferred", sa.BigInteger(), server_default="0", nullable=False
        ),
        sa.Column(
            "measurement_source",
            sa.String(length=32),
            server_default="authorized",
            nullable=False,
        ),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["export_id"], ["media_vault_exports.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["media_item_id"], ["media_items.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["vault_id"], ["media_vaults.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    for column_name in (
        "vault_id",
        "media_item_id",
        "actor_id",
        "status",
        "created_at",
    ):
        op.create_index(
            f"ix_media_transfer_logs_{column_name}",
            "media_transfer_logs",
            [column_name],
            unique=False,
        )
    op.create_index(
        "ix_media_transfer_logs_month_direction",
        "media_transfer_logs",
        ["created_at", "direction"],
        unique=False,
    )

    op.create_table(
        "media_takedown_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("media_item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requested_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "status", sa.String(length=24), server_default="open", nullable=False
        ),
        sa.Column("resolved_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("resolution_notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["media_item_id"], ["media_items.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for column_name in ("media_item_id", "requested_by", "status"):
        op.create_index(
            f"ix_media_takedown_requests_{column_name}",
            "media_takedown_requests",
            [column_name],
            unique=False,
        )


def downgrade() -> None:
    op.drop_table("media_takedown_requests")
    op.drop_table("media_transfer_logs")
    op.drop_table("media_vault_exports")
    for column_name in (
        "soft_deleted_at",
        "consent_status",
        "review_status",
        "checksum_sha256",
        "client_fingerprint",
        "upload_batch_id",
        "vault_id",
    ):
        op.drop_index(f"ix_media_items_{column_name}", table_name="media_items")
    for column_name in (
        "soft_deleted_at",
        "published_at",
        "published_media_id",
        "duplicate_of_id",
        "thumbnail_object_key",
        "proxy_object_key",
        "proxy_url",
        "reviewed_at",
        "reviewed_by",
        "rejection_reason",
        "review_notes",
        "rating",
        "consent_status",
        "review_status",
        "processing_status",
        "captured_at",
        "multipart_upload_id",
        "checksum_sha256",
        "client_fingerprint",
        "size_bytes",
        "content_type",
        "original_filename",
        "bucket_type",
        "object_key",
        "upload_batch_id",
        "vault_id",
    ):
        op.drop_column("media_items", column_name)
    op.drop_table("media_upload_batches")
    op.drop_table("media_vault_guest_links")
    op.drop_table("media_vault_grants")
    op.drop_table("media_vaults")
