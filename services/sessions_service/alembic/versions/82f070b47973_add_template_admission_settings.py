"""add_template_admission_settings

Hand-written migration — autogenerate could not reach the configured development
database. Single additive JSONB column matching SessionTemplate.admission_settings.

Revision ID: 82f070b47973
Revises: bd99897b8e40
Create Date: 2026-09-27 07:56:20.587391
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "82f070b47973"
down_revision = "bd99897b8e40"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "session_templates",
        sa.Column(
            "admission_settings",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("session_templates", "admission_settings")
