"""Store first-party content-attributed registrations in Reporting.

Revision ID: a08e16d7c9fa
Revises: c9f2a7e41b30
"""

from alembic import op
import sqlalchemy as sa

revision = "a08e16d7c9fa"
down_revision = "c9f2a7e41b30"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "content_acquisition_snapshots",
        sa.Column("id", sa.UUID(), nullable=False, primary_key=True),
        sa.Column("content_id", sa.UUID(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("registrations", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source", sa.String(32), nullable=False, server_default="members_registration"),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "content_id", "period_start", "period_end",
            name="uq_content_acquisition_period",
        ),
    )


def downgrade() -> None:
    op.drop_table("content_acquisition_snapshots")
