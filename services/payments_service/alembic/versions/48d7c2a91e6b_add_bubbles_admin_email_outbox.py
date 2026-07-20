"""add Bubbles admin email outbox

Revision ID: 48d7c2a91e6b
Revises: 36dbbd8ca925
Create Date: 2026-07-20
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "48d7c2a91e6b"
down_revision: Union[str, None] = "36dbbd8ca925"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "payments",
        sa.Column(
            "admin_payment_notification_required",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )
    op.create_table(
        "payment_admin_email_logs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("payment_id", sa.UUID(), nullable=False),
        sa.Column("recipient_email", sa.String(), nullable=False),
        sa.Column(
            "delivery_status",
            sa.String(length=20),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_message", sa.Text()),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["payment_id"], ["payments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "payment_id",
            "recipient_email",
            name="uq_payment_admin_email_logs_payment_recipient",
        ),
    )
    op.create_index(
        "ix_payment_admin_email_logs_payment_id",
        "payment_admin_email_logs",
        ["payment_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_payment_admin_email_logs_payment_id",
        table_name="payment_admin_email_logs",
    )
    op.drop_table("payment_admin_email_logs")
    op.drop_column("payments", "admin_payment_notification_required")
