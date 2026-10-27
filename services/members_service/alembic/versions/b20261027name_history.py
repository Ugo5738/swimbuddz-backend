"""Audit self-service member name changes.

Revision ID: b20261027name
Revises: b20261015terms
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "b20261027name"
down_revision = "b20261015terms"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "member_name_changes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("member_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("members.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("actor_auth_id", sa.String(160), nullable=False),
        sa.Column("old_first_name", sa.String(100), nullable=False),
        sa.Column("old_last_name", sa.String(100), nullable=False),
        sa.Column("new_first_name", sa.String(100), nullable=False),
        sa.Column("new_last_name", sa.String(100), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_member_name_changes_member_id", "member_name_changes", ["member_id"])


def downgrade() -> None:
    op.drop_index("ix_member_name_changes_member_id", table_name="member_name_changes")
    op.drop_table("member_name_changes")
