"""add content AI audit fields

Revision ID: e8f4b91a2c6d
Revises: 7c3f21b7d804
Create Date: 2026-07-13 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "e8f4b91a2c6d"
down_revision = "7c3f21b7d804"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("content_posts", sa.Column("featured_image_prompt", sa.Text(), nullable=True))
    op.add_column("content_posts", sa.Column("ai_request_id", sa.UUID(), nullable=True))
    op.add_column(
        "content_posts", sa.Column("ai_context_version", sa.String(length=100), nullable=True)
    )
    op.add_column(
        "content_posts", sa.Column("ai_model_used", sa.String(length=160), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("content_posts", "ai_model_used")
    op.drop_column("content_posts", "ai_context_version")
    op.drop_column("content_posts", "ai_request_id")
    op.drop_column("content_posts", "featured_image_prompt")
