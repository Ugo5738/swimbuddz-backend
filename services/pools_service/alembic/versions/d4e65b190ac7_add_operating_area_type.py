"""add operating area type

Revision ID: d4e65b190ac7
Revises: c3b752a71f42
Create Date: 2026-08-10
"""

import sqlalchemy as sa
from alembic import op


revision = "d4e65b190ac7"
down_revision = "c3b752a71f42"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "operating_areas",
        sa.Column(
            "area_type",
            sa.String(length=32),
            server_default="locality",
            nullable=False,
        ),
    )
    op.execute(
        """
        UPDATE operating_areas
        SET area_type = CASE
            WHEN parent_id IS NULL THEN 'country'
            ELSE 'locality'
        END
        """
    )


def downgrade() -> None:
    op.drop_column("operating_areas", "area_type")
