"""add quarterly reporting semantics version

Revision ID: c9f2a7e41b30
Revises: b8e1f3a52d40
Create Date: 2026-10-05 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "c9f2a7e41b30"
down_revision = "b8e1f3a52d40"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "quarterly_snapshots",
        sa.Column(
            "semantics_version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )


def downgrade() -> None:
    op.drop_column("quarterly_snapshots", "semantics_version")
