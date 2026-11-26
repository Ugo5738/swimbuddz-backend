"""Add Store procurement settlements, credit repayments, ledger sync & receipt media IDs.

Revision ID: f181126a7303
Revises: f181126a7302
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "f181126a7303"
down_revision = "f181126a7302"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "store_goods_receipts",
        sa.Column("evidence_media_id", UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "store_goods_receipts",
        sa.Column("ledger_entry_id", UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "store_goods_receipts",
        sa.Column(
            "ledger_sync_status",
            sa.String(24),
            nullable=False,
            server_default="not_posted",
        ),
    )

    op.add_column(
        "store_supplier_invoices",
        sa.Column("evidence_media_id", UUID(as_uuid=True), nullable=True),
    )

    op.add_column(
        "store_trade_receivables",
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "store_trade_receivables",
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "store_trade_receivables",
        sa.Column("ledger_entry_id", UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "store_trade_receivables",
        sa.Column(
            "ledger_sync_status",
            sa.String(24),
            nullable=False,
            server_default="not_posted",
        ),
    )

    op.add_column(
        "store_opening_reconciliations",
        sa.Column("evidence_media_id", UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "store_opening_reconciliations",
        sa.Column("ledger_entry_id", UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "store_opening_reconciliations",
        sa.Column(
            "ledger_sync_status",
            sa.String(24),
            nullable=False,
            server_default="not_posted",
        ),
    )

    op.create_table(
        "store_supplier_invoice_settlements",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "invoice_id",
            UUID(as_uuid=True),
            sa.ForeignKey("store_supplier_invoices.id"),
            nullable=False,
        ),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("bank_reference", sa.String(180), nullable=False),
        sa.Column("treasury_request_id", UUID(as_uuid=True), nullable=True),
        sa.Column("ledger_journal_entry_id", UUID(as_uuid=True), nullable=True),
        sa.Column("evidence_media_id", UUID(as_uuid=True), nullable=True),
        sa.Column("settled_by", sa.String(255), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "invoice_id",
            "bank_reference",
            name="uq_store_supplier_invoice_settlement_ref",
        ),
        sa.CheckConstraint(
            "amount_minor > 0",
            name="ck_store_supplier_invoice_settlement_positive",
        ),
    )
    op.create_index(
        "ix_store_supplier_invoice_settlements_invoice_id",
        "store_supplier_invoice_settlements",
        ["invoice_id"],
    )

    op.create_table(
        "store_trade_receivable_payments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "receivable_id",
            UUID(as_uuid=True),
            sa.ForeignKey("store_trade_receivables.id"),
            nullable=False,
        ),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("bank_reference", sa.String(180), unique=True, nullable=False),
        sa.Column(
            "bank_account_ref",
            sa.String(80),
            nullable=False,
            server_default="bank_operating_ngn",
        ),
        sa.Column("evidence_media_id", UUID(as_uuid=True), nullable=True),
        sa.Column("ledger_entry_id", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "ledger_sync_status",
            sa.String(24),
            nullable=False,
            server_default="not_posted",
        ),
        sa.Column("verified_by", sa.String(255), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "amount_minor > 0",
            name="ck_store_trade_receivable_payment_positive",
        ),
    )
    op.create_index(
        "ix_store_trade_receivable_payments_receivable_id",
        "store_trade_receivable_payments",
        ["receivable_id"],
    )


def downgrade():
    op.drop_index(
        "ix_store_trade_receivable_payments_receivable_id",
        table_name="store_trade_receivable_payments",
    )
    op.drop_table("store_trade_receivable_payments")
    op.drop_index(
        "ix_store_supplier_invoice_settlements_invoice_id",
        table_name="store_supplier_invoice_settlements",
    )
    op.drop_table("store_supplier_invoice_settlements")

    op.drop_column("store_opening_reconciliations", "ledger_sync_status")
    op.drop_column("store_opening_reconciliations", "ledger_entry_id")
    op.drop_column("store_opening_reconciliations", "evidence_media_id")

    op.drop_column("store_trade_receivables", "ledger_sync_status")
    op.drop_column("store_trade_receivables", "ledger_entry_id")
    op.drop_column("store_trade_receivables", "settled_at")
    op.drop_column("store_trade_receivables", "approved_at")

    op.drop_column("store_supplier_invoices", "evidence_media_id")

    op.drop_column("store_goods_receipts", "ledger_sync_status")
    op.drop_column("store_goods_receipts", "ledger_entry_id")
    op.drop_column("store_goods_receipts", "evidence_media_id")
