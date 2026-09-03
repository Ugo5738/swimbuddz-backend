"""Add searchable labels to vault media.

Revision ID: 4e71c6a3d920
Revises: 19b7e2a4c6d8
Create Date: 2026-09-03
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "4e71c6a3d920"
down_revision: Union[str, None] = "19b7e2a4c6d8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "media_items",
        sa.Column(
            "vault_labels",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_media_items_vault_labels_gin",
        "media_items",
        ["vault_labels"],
        unique=False,
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_media_items_vault_labels_gin", table_name="media_items")
    op.drop_column("media_items", "vault_labels")
