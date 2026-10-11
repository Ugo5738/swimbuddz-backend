"""Persist extended quarterly metrics without fabricating legacy values.

Revision ID: d4e8f1a6b209
Revises: c9f2a7e41b30
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "d4e8f1a6b209"
down_revision = "c9f2a7e41b30"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "member_quarterly_reports",
        sa.Column("activity_details", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "community_quarterly_stats",
        sa.Column("ecosystem_stats", postgresql.JSONB(), nullable=True),
    )


def downgrade():
    op.drop_column("community_quarterly_stats", "ecosystem_stats")
    op.drop_column("member_quarterly_reports", "activity_details")
