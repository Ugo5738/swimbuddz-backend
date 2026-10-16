"""Associate featured swimmers with existing vault media.

Revision ID: e16a4e8b2970
Revises: d16c8b019ea4
"""

from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e16a4e8b2970"
down_revision: Union[str, None] = "d16c8b019ea4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "media_vault_featured_members",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "media_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("media_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("member_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "media_item_id", "member_id", name="uq_vault_featured_member"
        ),
    )
    op.create_index(
        "ix_media_vault_featured_members_media_item_id",
        "media_vault_featured_members",
        ["media_item_id"],
    )
    op.create_index(
        "ix_vault_featured_members_member",
        "media_vault_featured_members",
        ["member_id", "media_item_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_vault_featured_members_member", table_name="media_vault_featured_members"
    )
    op.drop_index(
        "ix_media_vault_featured_members_media_item_id",
        table_name="media_vault_featured_members",
    )
    op.drop_table("media_vault_featured_members")
