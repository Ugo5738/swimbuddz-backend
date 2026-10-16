"""Vault audience defaults and member original download permissions.

Revision ID: d16c8b019ea4
Revises: c815f2ae4100
"""

from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "d16c8b019ea4"
down_revision: Union[str, None] = "c815f2ae4100"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "media_vaults",
        sa.Column(
            "default_session_visibility",
            sa.String(24),
            nullable=False,
            server_default="selected_members",
        ),
    )
    op.add_column(
        "media_vaults",
        sa.Column(
            "default_allow_member_clips",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "media_vaults",
        sa.Column(
            "default_allow_member_downloads",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "media_items",
        sa.Column(
            "allow_member_downloads",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("media_items", "allow_member_downloads")
    op.drop_column("media_vaults", "default_allow_member_downloads")
    op.drop_column("media_vaults", "default_allow_member_clips")
    op.drop_column("media_vaults", "default_session_visibility")
