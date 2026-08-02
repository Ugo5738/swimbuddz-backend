"""Link presentation media variants to their preserved originals.

Revision ID: 19b7e2a4c6d8
Revises: d7c2f4a8b901
Create Date: 2026-08-02
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "19b7e2a4c6d8"
down_revision: Union[str, None] = "d7c2f4a8b901"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "media_items",
        sa.Column(
            "source_media_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_index(
        op.f("ix_media_items_source_media_id"),
        "media_items",
        ["source_media_id"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_media_items_source_media_id",
        "media_items",
        "media_items",
        ["source_media_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_media_items_source_media_id",
        "media_items",
        type_="foreignkey",
    )
    op.drop_index(op.f("ix_media_items_source_media_id"), table_name="media_items")
    op.drop_column("media_items", "source_media_id")
