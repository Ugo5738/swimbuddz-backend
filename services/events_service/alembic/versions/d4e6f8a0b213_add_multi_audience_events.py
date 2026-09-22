"""Add canonical multi-audience fields to events and templates.

Revision ID: d4e6f8a0b213
Revises: a3b5c7d9e012
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg


revision = "d4e6f8a0b213"
down_revision = "a3b5c7d9e012"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("events", "event_templates"):
        op.add_column(
            table,
            sa.Column(
                "primary_audience",
                sa.String(),
                nullable=False,
                server_default="community",
            ),
        )
        op.add_column(
            table,
            sa.Column(
                "audiences",
                pg.JSONB(),
                nullable=False,
                server_default=sa.text("'[\"community\"]'::jsonb"),
            ),
        )
        op.execute(
            sa.text(
                f"UPDATE {table} SET primary_audience = audience, "
                "audiences = jsonb_build_array(audience)"
            )
        )
        op.create_check_constraint(
            f"ck_{table}_primary_audience",
            table,
            "primary_audience IN ('community','club','academy')",
        )
        op.create_check_constraint(
            f"ck_{table}_audiences_array",
            table,
            "jsonb_typeof(audiences) = 'array' "
            "AND jsonb_array_length(audiences) > 0 "
            "AND audiences <@ '[\"community\",\"club\",\"academy\"]'::jsonb "
            "AND audiences @> jsonb_build_array(primary_audience)",
        )


def downgrade():
    for table in ("event_templates", "events"):
        op.execute(
            sa.text(f"UPDATE {table} SET audience = primary_audience")
        )
        op.drop_constraint(f"ck_{table}_audiences_array", table, type_="check")
        op.drop_constraint(f"ck_{table}_primary_audience", table, type_="check")
        op.drop_column(table, "audiences")
        op.drop_column(table, "primary_audience")
