"""add wallet holds

Revision ID: e72d4c8a9130
Revises: d11870c4f761
Create Date: 2026-07-14
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "e72d4c8a9130"
down_revision = "d11870c4f761"
branch_labels = None
depends_on = None


def upgrade() -> None:
    hold_status = postgresql.ENUM(
        "held",
        "captured",
        "released",
        "expired",
        name="wallet_hold_status_enum",
        create_type=False,
    )
    hold_status.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "wallet_holds",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("wallet_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("member_auth_id", sa.String(), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("status", hold_status, nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("service_source", sa.String(), nullable=False),
        sa.Column("reference_type", sa.String(), nullable=True),
        sa.Column("reference_id", sa.String(), nullable=True),
        sa.Column(
            "wallet_transaction_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("amount > 0", name="ck_wallet_hold_amount_positive"),
        sa.ForeignKeyConstraint(["wallet_id"], ["wallets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["wallet_transaction_id"], ["wallet_transactions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
        sa.UniqueConstraint("wallet_transaction_id"),
    )
    op.create_index("ix_wallet_holds_wallet_id", "wallet_holds", ["wallet_id"])
    op.create_index(
        "ix_wallet_holds_member_auth_id", "wallet_holds", ["member_auth_id"]
    )
    op.create_index("ix_wallet_holds_status", "wallet_holds", ["status"])
    op.create_index("ix_wallet_holds_expires_at", "wallet_holds", ["expires_at"])


def downgrade() -> None:
    op.drop_table("wallet_holds")
    postgresql.ENUM(name="wallet_hold_status_enum").drop(op.get_bind(), checkfirst=True)
