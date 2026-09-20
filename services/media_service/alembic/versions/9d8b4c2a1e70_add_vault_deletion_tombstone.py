"""Allow safe removal of media vaults without destroying originals.

Revision ID: 9d8b4c2a1e70
Revises: 4e71c6a3d920
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "9d8b4c2a1e70"
down_revision: Union[str, None] = "4e71c6a3d920"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("media_vaults", sa.Column("deleted_at", sa.DateTime(timezone=True)))
    op.create_index("ix_media_vaults_deleted_at", "media_vaults", ["deleted_at"])


def downgrade() -> None:
    op.drop_index("ix_media_vaults_deleted_at", table_name="media_vaults")
    op.drop_column("media_vaults", "deleted_at")
