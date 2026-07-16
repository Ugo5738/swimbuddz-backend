"""harden content email delivery

Revision ID: f2a6d9c40e31
Revises: e8f4b91a2c6d
Create Date: 2026-07-13
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f2a6d9c40e31"
down_revision: Union[str, None] = "e8f4b91a2c6d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "notification_preferences",
        sa.Column(
            "email_content_updates",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
    )
    op.add_column(
        "content_post_email_logs",
        sa.Column(
            "attempt_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.add_column(
        "content_post_email_logs",
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        "UPDATE content_post_email_logs "
        "SET attempt_count = 1, last_attempt_at = COALESCE(sent_at, updated_at)"
    )


def downgrade() -> None:
    op.drop_column("content_post_email_logs", "last_attempt_at")
    op.drop_column("content_post_email_logs", "attempt_count")
    op.drop_column("notification_preferences", "email_content_updates")
