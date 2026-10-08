"""Support Beyond the Pool episode metadata on published content posts.

Revision ID: c53a81f902d4
Revises: b7d4e2a19c63
"""
from alembic import op
import sqlalchemy as sa

revision = "c53a81f902d4"
down_revision = "b7d4e2a19c63"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.add_column("content_posts", sa.Column("video_url", sa.String(500), nullable=True))
    op.add_column("content_posts", sa.Column("episode_number", sa.Integer(), nullable=True))
    op.add_column("content_posts", sa.Column("guest_names", sa.String(500), nullable=True))
    op.create_check_constraint("ck_content_episode_number_positive", "content_posts", "episode_number IS NULL OR episode_number > 0")

def downgrade() -> None:
    op.drop_constraint("ck_content_episode_number_positive", "content_posts", type_="check")
    op.drop_column("content_posts", "guest_names")
    op.drop_column("content_posts", "episode_number")
    op.drop_column("content_posts", "video_url")
