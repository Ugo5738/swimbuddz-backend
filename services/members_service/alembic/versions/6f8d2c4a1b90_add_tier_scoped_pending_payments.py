"""add tier-scoped pending membership payments

Revision ID: 6f8d2c4a1b90
Revises: 5e7751f89eaf
Create Date: 2026-07-13
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "6f8d2c4a1b90"
down_revision: Union[str, None] = "5e7751f89eaf"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "member_memberships",
        sa.Column(
            "pending_tier_payments",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("member_memberships", "pending_tier_payments")
