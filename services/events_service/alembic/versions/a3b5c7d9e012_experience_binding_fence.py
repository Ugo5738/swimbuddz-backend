"""Fence delayed Experience binding requests after recovery.

Revision ID: a3b5c7d9e012
Revises: f2a4c6e8b901
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = "a3b5c7d9e012"
down_revision = "f2a4c6e8b901"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "experience_binding_operations",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("offering_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("event_ids", pg.JSONB(), nullable=False),
    )


def downgrade():
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM experience_binding_operations) THEN RAISE EXCEPTION 'Preserve Experience binding recovery records before downgrade'; END IF; END $$"
    )
    op.drop_table("experience_binding_operations")
