"""add entitlement application log

Revision ID: c9a1e6f24b70
Revises: 6f8d2c4a1b90
Create Date: 2026-07-13
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c9a1e6f24b70"
down_revision: Union[str, None] = "6f8d2c4a1b90"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "member_entitlement_applications",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("member_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("tier", sa.String(length=20), nullable=False),
        sa.Column("action", sa.String(length=50), nullable=False),
        sa.Column("source_reference", sa.String(length=160), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["member_id"],
            ["members.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_member_entitlement_applications_idempotency_key",
        ),
    )
    op.create_index(
        op.f("ix_member_entitlement_applications_member_id"),
        "member_entitlement_applications",
        ["member_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_member_entitlement_applications_member_id"),
        table_name="member_entitlement_applications",
    )
    op.drop_table("member_entitlement_applications")
