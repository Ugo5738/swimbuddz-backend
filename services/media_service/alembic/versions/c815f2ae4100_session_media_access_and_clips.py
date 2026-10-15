"""Session-aware media visibility, personal vaults, expiring clips.

Revision ID: c815f2ae4100
Revises: f26b17d9c803
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c815f2ae4100"
down_revision: Union[str, None] = "f26b17d9c803"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "media_vaults",
        sa.Column("owner_member_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        "ix_media_vaults_owner_member_id", "media_vaults", ["owner_member_id"]
    )
    op.create_unique_constraint(
        "uq_media_vaults_owner_member_id", "media_vaults", ["owner_member_id"]
    )
    op.add_column(
        "media_items",
        sa.Column("source_session_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        "ix_media_items_source_session_id", "media_items", ["source_session_id"]
    )
    op.add_column(
        "media_items",
        sa.Column(
            "session_visibility",
            sa.String(24),
            nullable=False,
            server_default="selected_members",
        ),
    )
    op.add_column(
        "media_items",
        sa.Column(
            "allow_member_clips",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_index(
        "ix_media_items_session_visibility", "media_items", ["session_visibility"]
    )
    op.create_table(
        "media_vault_clips",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "media_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("media_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("member_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("start_ms", sa.Integer(), nullable=False),
        sa.Column("end_ms", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("object_key", sa.String(1024)),
        sa.Column("error_message", sa.String(500)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_media_vault_clips_media_item_id", "media_vault_clips", ["media_item_id"]
    )
    op.create_index(
        "ix_media_vault_clips_member_id", "media_vault_clips", ["member_id"]
    )
    op.create_index(
        "ix_vault_clips_member_created",
        "media_vault_clips",
        ["member_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_vault_clips_member_created", table_name="media_vault_clips")
    op.drop_index("ix_media_vault_clips_member_id", table_name="media_vault_clips")
    op.drop_index("ix_media_vault_clips_media_item_id", table_name="media_vault_clips")
    op.drop_table("media_vault_clips")
    op.drop_index("ix_media_items_session_visibility", table_name="media_items")
    op.drop_column("media_items", "allow_member_clips")
    op.drop_column("media_items", "session_visibility")
    op.drop_index("ix_media_items_source_session_id", table_name="media_items")
    op.drop_column("media_items", "source_session_id")
    op.drop_constraint(
        "uq_media_vaults_owner_member_id", "media_vaults", type_="unique"
    )
    op.drop_index("ix_media_vaults_owner_member_id", table_name="media_vaults")
    op.drop_column("media_vaults", "owner_member_id")
