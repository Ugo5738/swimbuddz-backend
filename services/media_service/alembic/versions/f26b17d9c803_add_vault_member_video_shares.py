"""Grant members access to consent-cleared session videos.

Revision ID: f26b17d9c803
Revises: 9d8b4c2a1e70
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f26b17d9c803"
down_revision: Union[str, None] = "9d8b4c2a1e70"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "media_vault_member_shares",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("media_item_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("media_items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("member_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("media_item_id", "member_id", name="uq_vault_media_member_share"),
    )
    op.create_index("ix_media_vault_member_shares_media_item_id",
                    "media_vault_member_shares", ["media_item_id"])
    op.create_index("ix_media_vault_member_shares_member_id",
                    "media_vault_member_shares", ["member_id"])
    op.create_index("ix_vault_media_shares_member_active",
                    "media_vault_member_shares", ["member_id", "revoked_at"])


def downgrade() -> None:
    op.drop_index("ix_vault_media_shares_member_active", table_name="media_vault_member_shares")
    op.drop_index("ix_media_vault_member_shares_member_id", table_name="media_vault_member_shares")
    op.drop_index("ix_media_vault_member_shares_media_item_id", table_name="media_vault_member_shares")
    op.drop_table("media_vault_member_shares")
