"""Merge Academy template context and recurrence migration histories.

Revision ID: a9b1c3d5e748
Revises: e7f9a1b3c425, f8a0b2c4d637

Both published revisions independently extend c6e8a0b2d914. Keeping them intact
allows upgrades from either branch as well as the previous production revision.
"""

revision = "a9b1c3d5e748"
down_revision = ("e7f9a1b3c425", "f8a0b2c4d637")
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
