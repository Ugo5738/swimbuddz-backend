"""add_sessions_list_query_index

Revision ID: a015127750f3
Revises: 7de6c9f14a20
Create Date: 2026-07-28 18:50:03.493471

Hand-written migration — Alembic autogeneration could not connect because the
configured development Supabase tenant is no longer available. The operations
mirror the indexes declared on ``Session.__table_args__``.
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = "a015127750f3"
down_revision = "7de6c9f14a20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_sessions_starts_at",
        "sessions",
        ["starts_at"],
        unique=False,
    )
    op.create_index(
        "ix_sessions_status_type_starts_at",
        "sessions",
        ["status", "session_type", "starts_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_sessions_status_type_starts_at",
        table_name="sessions",
    )
    op.drop_index(
        "ix_sessions_starts_at",
        table_name="sessions",
    )
