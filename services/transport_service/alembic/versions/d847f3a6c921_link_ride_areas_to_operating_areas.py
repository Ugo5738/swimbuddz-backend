"""link ride areas to canonical operating areas

Revision ID: d847f3a6c921
Revises: 6c14e28b973a
Create Date: 2026-08-10
"""

import sqlalchemy as sa
from alembic import op


revision = "d847f3a6c921"
down_revision = "6c14e28b973a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ride_areas", sa.Column("operating_area_id", sa.UUID(), nullable=True))
    op.create_index(
        "ix_ride_areas_operating_area_id",
        "ride_areas",
        ["operating_area_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_ride_areas_operating_area_id", table_name="ride_areas")
    op.drop_column("ride_areas", "operating_area_id")
