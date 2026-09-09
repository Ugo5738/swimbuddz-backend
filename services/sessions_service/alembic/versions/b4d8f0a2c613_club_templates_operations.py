"""Club location, explicit access and auditable schedule operations.

Revision ID: b4d8f0a2c613
Revises: b7d4e8f2a610
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = "b4d8f0a2c613"
down_revision = "b7d4e8f2a610"
branch_labels = depends_on = None


def upgrade():
    for table in ("sessions", "session_templates"):
        op.add_column(
            table,
            sa.Column(
                "club_access_mode",
                sa.String(24),
                nullable=False,
                server_default="plan_included",
            ),
        )
        op.create_check_constraint(
            f"ck_{table}_club_access_mode",
            table,
            "club_access_mode IN ('plan_included','active_club','paid_addon') AND (session_type = 'club' OR (club_id IS NULL AND club_access_mode = 'plan_included')) AND (club_access_mode = 'plan_included' OR club_id IS NOT NULL)",
        )
    op.add_column(
        "session_templates",
        sa.Column("pricing_settings", pg.JSONB(), nullable=False, server_default="{}"),
    )
    op.create_table(
        "club_schedule_operations",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("payload", pg.JSONB(), nullable=False),
        sa.Column("before", pg.JSONB(), nullable=False),
        sa.Column("after", pg.JSONB(), nullable=False),
        sa.Column("notification_status", sa.String(24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM club_schedule_operations) THEN RAISE EXCEPTION 'Preserve schedule-operation audit records before downgrade'; END IF; END $$"
    )
    op.drop_table("club_schedule_operations")
    op.drop_column("session_templates", "pricing_settings")
    for table in ("sessions", "session_templates"):
        op.drop_constraint(f"ck_{table}_club_access_mode", table, type_="check")
        op.drop_column(table, "club_access_mode")
