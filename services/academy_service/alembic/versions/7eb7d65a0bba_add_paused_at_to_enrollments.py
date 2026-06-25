"""add_paused_at_to_enrollments

Revision ID: 7eb7d65a0bba
Revises: 6c2aaedaed39
Create Date: 2026-06-24 18:00:31.280286
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "7eb7d65a0bba"
down_revision = "6c2aaedaed39"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add the resumable-pause marker to enrollments. (Unrelated index-name
    # drift on milestone_review_events that autogenerate also emitted was
    # intentionally dropped — out of scope for this migration.)
    op.add_column(
        "enrollments", sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("enrollments", "paused_at")
