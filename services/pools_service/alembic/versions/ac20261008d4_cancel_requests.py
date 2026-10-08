"""Paid Pool Access cancellation and refund review records.

Revision ID: ac20261008d4
Revises: ac20261008c3
"""

from alembic import op
import sqlalchemy as sa

revision = "ac20261008d4"
down_revision = "ac20261008c3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pool_access_cancellation_requests",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "booking_id",
            sa.UUID(),
            sa.ForeignKey("pool_access_bookings.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("requested_by", sa.String(255), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("admin_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("booking_id", name="uq_pool_access_cancellation_booking"),
        sa.CheckConstraint(
            "status IN ('requested', 'under_review', 'resolved', 'rejected')",
            name="ck_pool_access_cancellation_status",
        ),
    )


def downgrade() -> None:
    op.drop_table("pool_access_cancellation_requests")
