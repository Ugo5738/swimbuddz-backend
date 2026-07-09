"""add_pod_id_to_session_templates

Revision ID: 9f3a8d7c1b20
Revises: 2a116b947232
Create Date: 2026-07-08 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "9f3a8d7c1b20"
down_revision = "2a116b947232"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("session_templates", sa.Column("pod_id", sa.UUID(), nullable=True))
    op.create_index(
        op.f("ix_session_templates_pod_id"),
        "session_templates",
        ["pod_id"],
        unique=False,
    )
    op.create_check_constraint(
        "ck_session_templates_pod_only_for_club",
        "session_templates",
        "pod_id IS NULL OR session_type = 'club'",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_session_templates_pod_only_for_club",
        "session_templates",
        type_="check",
    )
    op.drop_index(op.f("ix_session_templates_pod_id"), table_name="session_templates")
    op.drop_column("session_templates", "pod_id")
