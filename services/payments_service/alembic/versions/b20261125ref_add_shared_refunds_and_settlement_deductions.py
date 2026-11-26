"""Add shared refunds table and Paystack settlement deductions column (ADR-024).

Hand-written migration — creates PostgreSQL enums (refund_method_enum,
refund_status_enum), CHECK constraints, indexes, and the deductions_minor
column on paystack_settlements cleanly without requiring live dev DB access.

Revision ID: b20261125ref
Revises: a20261009retro
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b20261125ref"
down_revision = "a20261009retro"
branch_labels = None
depends_on = None


def upgrade() -> None:
    refund_method_enum = postgresql.ENUM(
        "paystack_refund",
        "manual_bank_transfer",
        "wallet_bubbles",
        "store_credit",
        "mixed",
        name="refund_method_enum",
        create_type=False,
    )
    refund_status_enum = postgresql.ENUM(
        "requested",
        "approved",
        "initiated",
        "completed",
        "failed",
        "cancelled",
        name="refund_status_enum",
        create_type=False,
    )
    bind = op.get_bind()
    refund_method_enum.create(bind, checkfirst=True)
    refund_status_enum.create(bind, checkfirst=True)

    op.add_column(
        "paystack_settlements",
        sa.Column(
            "deductions_minor",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
    )

    op.create_table(
        "refunds",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("refund_reference", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(160), nullable=False),
        sa.Column(
            "payment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("payments.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("payment_reference", sa.String(128), nullable=True),
        sa.Column("member_auth_id", sa.String(255), nullable=False),
        sa.Column("payer_email", sa.String(255), nullable=True),
        sa.Column("source_service", sa.String(40), nullable=False),
        sa.Column("source_entity_type", sa.String(64), nullable=False),
        sa.Column("source_entity_id", sa.String(128), nullable=False),
        sa.Column("purpose", sa.String(64), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False, server_default="NGN"),
        sa.Column(
            "requested_total_kobo", sa.BigInteger(), nullable=False, server_default="0"
        ),
        sa.Column(
            "cash_refund_kobo", sa.BigInteger(), nullable=False, server_default="0"
        ),
        sa.Column("bubbles_refund", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "store_credit_refund_kobo",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("refund_method", refund_method_enum, nullable=False),
        sa.Column(
            "status", refund_status_enum, nullable=False, server_default="requested"
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("admin_note", sa.Text(), nullable=True),
        sa.Column("override_reason", sa.Text(), nullable=True),
        sa.Column("provider", sa.String(32), nullable=True),
        sa.Column("provider_refund_id", sa.String(128), nullable=True),
        sa.Column("provider_status", sa.String(64), nullable=True),
        sa.Column("provider_payload", postgresql.JSONB(), nullable=True),
        sa.Column("external_bank_reference", sa.String(128), nullable=True),
        sa.Column("recipient_bank_code", sa.String(32), nullable=True),
        sa.Column("recipient_bank_name", sa.String(128), nullable=True),
        sa.Column("recipient_account_number", sa.String(32), nullable=True),
        sa.Column("recipient_account_name", sa.String(255), nullable=True),
        sa.Column(
            "recipient_account_verified",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        sa.Column("wallet_refund_transaction_id", sa.String(128), nullable=True),
        sa.Column("store_credit_id", sa.String(128), nullable=True),
        sa.Column(
            "ledger_accrual_posted",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        sa.Column(
            "ledger_disbursement_posted",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        sa.Column("requested_by_auth_id", sa.String(255), nullable=True),
        sa.Column("approved_by_auth_id", sa.String(255), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("initiated_by_auth_id", sa.String(255), nullable=True),
        sa.Column("initiated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_by_auth_id", sa.String(255), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "requested_total_kobo >= 0", name="ck_refunds_requested_total_nonneg"
        ),
        sa.CheckConstraint("cash_refund_kobo >= 0", name="ck_refunds_cash_nonneg"),
        sa.CheckConstraint("bubbles_refund >= 0", name="ck_refunds_bubbles_nonneg"),
        sa.CheckConstraint(
            "store_credit_refund_kobo >= 0", name="ck_refunds_store_credit_nonneg"
        ),
    )
    op.create_index(
        "ix_refunds_refund_reference", "refunds", ["refund_reference"], unique=True
    )
    op.create_index(
        "ix_refunds_idempotency_key", "refunds", ["idempotency_key"], unique=True
    )
    op.create_index("ix_refunds_payment_id", "refunds", ["payment_id"])
    op.create_index("ix_refunds_payment_reference", "refunds", ["payment_reference"])
    op.create_index("ix_refunds_member_auth_id", "refunds", ["member_auth_id"])
    op.create_index("ix_refunds_source_service", "refunds", ["source_service"])
    op.create_index("ix_refunds_source_entity_id", "refunds", ["source_entity_id"])
    op.create_index("ix_refunds_status", "refunds", ["status"])
    op.create_index("ix_refunds_provider_refund_id", "refunds", ["provider_refund_id"])
    op.create_index(
        "ix_refunds_external_bank_reference", "refunds", ["external_bank_reference"]
    )
    op.create_index(
        "ix_refunds_source_lookup",
        "refunds",
        ["source_service", "source_entity_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_refunds_source_lookup", table_name="refunds")
    op.drop_index("ix_refunds_external_bank_reference", table_name="refunds")
    op.drop_index("ix_refunds_provider_refund_id", table_name="refunds")
    op.drop_index("ix_refunds_status", table_name="refunds")
    op.drop_index("ix_refunds_source_entity_id", table_name="refunds")
    op.drop_index("ix_refunds_source_service", table_name="refunds")
    op.drop_index("ix_refunds_member_auth_id", table_name="refunds")
    op.drop_index("ix_refunds_payment_reference", table_name="refunds")
    op.drop_index("ix_refunds_payment_id", table_name="refunds")
    op.drop_index("ix_refunds_idempotency_key", table_name="refunds")
    op.drop_index("ix_refunds_refund_reference", table_name="refunds")
    op.drop_table("refunds")
    op.drop_column("paystack_settlements", "deductions_minor")

    bind = op.get_bind()
    postgresql.ENUM(name="refund_status_enum").drop(bind, checkfirst=True)
    postgresql.ENUM(name="refund_method_enum").drop(bind, checkfirst=True)
