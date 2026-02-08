"""rename_volunteer_tables_to_legacy

Renames volunteer_roles and volunteer_interests tables to legacy_ prefix.
This is Phase 1 of migrating volunteer functionality from members_service
to the dedicated volunteer_service.

The legacy tables are preserved with all data intact. A separate migration
script (scripts/migrate/volunteer_data.py) will copy data into the new
volunteer_service tables. The legacy tables should only be dropped AFTER
confirming the data migration was successful.

Revision ID: a1b2c3d4e5f6
Revises: 8e23ec95cf22
Create Date: 2026-02-07 00:00:00.000000
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'a1b2c3d4e5f6'
down_revision = '8e23ec95cf22'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Rename tables — data is fully preserved, just under new names
    op.rename_table('volunteer_roles', 'legacy_volunteer_roles')
    op.rename_table('volunteer_interests', 'legacy_volunteer_interests')


def downgrade() -> None:
    # Reverse the rename — restore original table names
    op.rename_table('legacy_volunteer_interests', 'volunteer_interests')
    op.rename_table('legacy_volunteer_roles', 'volunteer_roles')
