"""Reconcile Academy heads from Alumni and Enrollment Management.

Revision ID: aa20261008merge
Revises: d82b6f9a14c0, b8f1e2c3d405
"""
revision = "aa20261008merge"
down_revision = ("d82b6f9a14c0", "b8f1e2c3d405")
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Both feature migrations are already applied along their branches."""


def downgrade() -> None:
    """Branch-specific migrations own their downgrade logic."""
