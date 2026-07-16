"""add content email dispatch state

Revision ID: b7d4e2a19c63
Revises: f2a6d9c40e31
Create Date: 2026-07-13
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b7d4e2a19c63"
down_revision: Union[str, None] = "f2a6d9c40e31"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "content_posts",
        sa.Column(
            "email_recipient_snapshot_at", sa.DateTime(timezone=True), nullable=True
        ),
    )
    op.add_column(
        "content_posts",
        sa.Column(
            "email_dispatch_last_attempt_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "content_posts",
        sa.Column(
            "email_dispatch_completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "content_posts",
        sa.Column("email_dispatch_last_error", sa.Text(), nullable=True),
    )
    op.add_column(
        "content_post_email_logs",
        sa.Column("recipient_email", sa.String(), nullable=True),
    )
    op.add_column(
        "content_post_email_logs",
        sa.Column("recipient_name", sa.String(), nullable=True),
    )

    # Existing published posts have already passed through the legacy delivery
    # path. Mark them complete so deployment cannot trigger a historical blast.
    op.execute(
        "UPDATE content_posts "
        "SET email_recipient_snapshot_at = COALESCE(published_at, updated_at), "
        "email_dispatch_last_attempt_at = COALESCE(published_at, updated_at), "
        "email_dispatch_completed_at = COALESCE(published_at, updated_at) "
        "WHERE is_published = true"
    )


def downgrade() -> None:
    op.drop_column("content_post_email_logs", "recipient_name")
    op.drop_column("content_post_email_logs", "recipient_email")
    op.drop_column("content_posts", "email_dispatch_last_error")
    op.drop_column("content_posts", "email_dispatch_completed_at")
    op.drop_column("content_posts", "email_dispatch_last_attempt_at")
    op.drop_column("content_posts", "email_recipient_snapshot_at")
