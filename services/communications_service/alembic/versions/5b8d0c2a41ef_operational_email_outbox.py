"""Add shared transactional operational email outbox.

Revision ID: 5b8d0c2a41ef
Revises: e2f6a9c1d308
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "5b8d0c2a41ef"
down_revision = "e2f6a9c1d308"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "operational_email_outbox",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_key", sa.String(255), nullable=False, unique=True),
        sa.Column("recipient_email", sa.String(254), nullable=True),
        sa.Column("member_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("notification_body", sa.Text(), nullable=True),
        sa.Column("html_body", sa.Text(), nullable=True),
        sa.Column("action_url", sa.String(500), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_operational_email_due",
        "operational_email_outbox",
        ["status", "next_attempt_at"],
    )
    # This is an internal queue; browser roles receive no RLS policy.
    op.execute("ALTER TABLE operational_email_outbox ENABLE ROW LEVEL SECURITY")


def downgrade():
    op.drop_index("ix_operational_email_due", table_name="operational_email_outbox")
    op.drop_table("operational_email_outbox")
