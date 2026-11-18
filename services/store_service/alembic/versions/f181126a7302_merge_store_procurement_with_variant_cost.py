"""Merge store procurement with the existing variant-cost migration.

Revision ID: f181126a7302
Revises: f181126a7301, d4a6c8e0f213

Both branches are required; no stock or cost data is rewritten.
"""
revision = "f181126a7302"
down_revision = ("f181126a7301", "d4a6c8e0f213")
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
