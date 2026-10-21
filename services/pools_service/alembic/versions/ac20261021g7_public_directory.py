"""Add curated public pool visibility and moderated reviews.

Revision ID: ac20261021g7
Revises: ac20261008f6
"""
import sqlalchemy as sa
from alembic import op

revision = "ac20261021g7"
down_revision = "ac20261008f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("pools", sa.Column("directory_published", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table(
        "pool_directory_reviews",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("pool_id", sa.UUID(), sa.ForeignKey("pools.id", ondelete="CASCADE"), nullable=False),
        sa.Column("author_auth_id", sa.String(255), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("review", sa.Text(), nullable=False),
        sa.Column("moderation_status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("pool_id", "author_auth_id", name="uq_pool_review_author"),
        sa.CheckConstraint("rating BETWEEN 1 AND 5", name="ck_pool_review_rating"),
        sa.CheckConstraint("moderation_status IN ('pending','approved','rejected')", name="ck_pool_review_status"),
    )
    op.create_index("ix_pool_directory_reviews_pool_id", "pool_directory_reviews", ["pool_id"])


def downgrade() -> None:
    op.drop_index("ix_pool_directory_reviews_pool_id", table_name="pool_directory_reviews")
    op.drop_table("pool_directory_reviews")
    op.drop_column("pools", "directory_published")
