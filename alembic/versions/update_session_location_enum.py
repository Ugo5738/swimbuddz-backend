"""update session location enum

Revision ID: update_session_location_enum
Revises: previous_revision_id
Create Date: 2025-12-01 18:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'update_session_location_enum'
down_revision: Union[str, None] = '6b1d29fce9bc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Postgres specific: Update the enum type to the new lower-case values and
    # migrate any existing rows from legacy values.
    op.execute("ALTER TYPE session_location_enum ADD VALUE IF NOT EXISTS 'sunfit_pool'")
    op.execute("ALTER TYPE session_location_enum ADD VALUE IF NOT EXISTS 'rowe_park_pool'")
    op.execute("ALTER TYPE session_location_enum ADD VALUE IF NOT EXISTS 'federal_palace_pool'")

    # Normalize existing data to the new enum values
    op.execute(
        """
        UPDATE sessions
        SET location = CASE
            WHEN location IN ('MAIN_POOL', 'DIVING_POOL', 'KIDS_POOL', 'SUNFIT_POOL') THEN 'sunfit_pool'
            WHEN location = 'ROWE_PARK_POOL' THEN 'rowe_park_pool'
            WHEN location = 'FEDERAL_PALACE_POOL' THEN 'federal_palace_pool'
            WHEN location = 'OPEN_WATER' THEN 'open_water'
            ELSE location
        END
        """
    )


def downgrade() -> None:
    # Downgrade is complex for Enums (requires creating new type, migrating data, dropping old type)
    # For now we skip strict downgrade for enum values
    pass
