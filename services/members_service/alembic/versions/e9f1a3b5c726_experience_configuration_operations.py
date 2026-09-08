"""Recoverable Experience configuration operations.

Revision ID: e9f1a3b5c726
Revises: d8e0f2a4b615
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = "e9f1a3b5c726"
down_revision = "d8e0f2a4b615"
branch_labels = depends_on = None


def upgrade():
    op.add_column(
        "club_plan_versions",
        sa.Column("source_template_id", pg.UUID(as_uuid=True), nullable=True),
    )
    op.create_table(
        "experience_configuration_operations",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "offering_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("community_experience_offerings.id"),
            nullable=False,
        ),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("old_event_ids", pg.JSONB(), nullable=False),
        sa.Column("request", pg.JSONB(), nullable=False),
        sa.Column("error", sa.String(500)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_experience_configuration_operations_offering_id",
        "experience_configuration_operations",
        ["offering_id"],
    )


def downgrade():
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM experience_configuration_operations) THEN RAISE EXCEPTION 'Preserve Experience operation audit records before downgrade'; END IF; END $$"
    )
    op.drop_table("experience_configuration_operations")
    op.drop_column("club_plan_versions", "source_template_id")
