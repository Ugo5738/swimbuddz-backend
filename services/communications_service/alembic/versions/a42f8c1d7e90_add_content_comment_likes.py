"""Add member likes for article comments.

Revision ID: a42f8c1d7e90
Revises: d912a6f40b77
"""

from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a42f8c1d7e90"
down_revision: Union[str, None] = "d912a6f40b77"
branch_labels: Union[str, list[str], None] = None
depends_on: Union[str, list[str], None] = None


def upgrade() -> None:
    op.create_table(
        "content_comment_likes",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "comment_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "member_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["comment_id"],
            ["content_comments.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "comment_id",
            "member_id",
            name="uq_content_comment_likes_comment_member",
        ),
    )
    op.create_index(
        "ix_content_comment_likes_comment_id",
        "content_comment_likes",
        ["comment_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_content_comment_likes_comment_id",
        table_name="content_comment_likes",
    )
    op.drop_table("content_comment_likes")
