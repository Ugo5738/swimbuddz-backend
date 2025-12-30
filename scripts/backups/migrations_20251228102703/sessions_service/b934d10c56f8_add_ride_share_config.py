"""add ride_share_config

Revision ID: b934d10c56f8
Revises: a934d10c56f7
Create Date: 2025-12-02 12:00:00.000000

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "b934d10c56f8"
down_revision = "a934d10c56f7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "session_templates",
        sa.Column(
            "ride_share_config", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
    )


def downgrade() -> None:
    op.drop_column("session_templates", "ride_share_config")
