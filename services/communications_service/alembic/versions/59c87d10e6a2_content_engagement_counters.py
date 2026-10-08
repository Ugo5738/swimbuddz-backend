"""Aggregate anonymous content counters.

Revision ID: 59c87d10e6a2
Revises: c53a81f902d4
"""
from alembic import op
import sqlalchemy as sa

revision = "59c87d10e6a2"
down_revision = "c53a81f902d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "content_engagement",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("post_id", sa.UUID(), sa.ForeignKey("content_posts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("source", sa.String(48), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("post_id", "event_type", "source", name="uq_content_engagement_bucket"),
        sa.CheckConstraint("total >= 0", name="ck_content_engagement_nonnegative"),
    )

def downgrade() -> None:
    op.drop_table("content_engagement")
