"""Add Store procurement settlements, credit repayments, ledger sync & receipt media IDs.

Revision ID: f181126a7303
Revises: f181126a7302

Earlier revision ``f181126a7301`` creates its initial procurement tables from live
SQLAlchemy model metadata, so on a fresh database these additive columns already
exist when ``f181126a7303`` executes, whereas existing databases upgraded from
``f181126a7302`` need them added. Guarding column/table/index operations with
``sqlalchemy.inspect`` keeps this migration safe and idempotent in both cases.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "f181126a7303"
down_revision = "f181126a7302"
branch_labels = None
depends_on = None


def _existing_columns(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(table_name):
        return set()
    return {col["name"] for col in inspector.get_columns(table_name)}


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    if column.name not in _existing_columns(table_name):
        op.add_column(table_name, column)


def _drop_column_if_exists(table_name: str, column_name: str) -> None:
    if column_name in _existing_columns(table_name):
        op.drop_column(table_name, column_name)


def _existing_indexes(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(table_name):
        return set()
    return {idx["name"] for idx in inspector.get_indexes(table_name) if idx.get("name")}


def upgrade():
    _add_column_if_missing(
        "store_goods_receipts",
        sa.Column("evidence_media_id", UUID(as_uuid=True), nullable=True),
    )
    _add_column_if_missing(
        "store_goods_receipts",
        sa.Column("ledger_entry_id", UUID(as_uuid=True), nullable=True),
    )
    _add_column_if_missing(
        "store_goods_receipts",
        sa.Column(
            "ledger_sync_status",
            sa.String(24),
            nullable=False,
            server_default="not_posted",
        ),
    )

    _add_column_if_missing(
        "store_supplier_invoices",
        sa.Column("evidence_media_id", UUID(as_uuid=True), nullable=True),
    )

    _add_column_if_missing(
        "store_trade_receivables",
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
    )
    _add_column_if_missing(
        "store_trade_receivables",
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
    )
    _add_column_if_missing(
        "store_trade_receivables",
        sa.Column("ledger_entry_id", UUID(as_uuid=True), nullable=True),
    )
    _add_column_if_missing(
        "store_trade_receivables",
        sa.Column(
            "ledger_sync_status",
            sa.String(24),
            nullable=False,
            server_default="not_posted",
        ),
    )

    _add_column_if_missing(
        "store_opening_reconciliations",
        sa.Column("evidence_media_id", UUID(as_uuid=True), nullable=True),
    )
    _add_column_if_missing(
        "store_opening_reconciliations",
        sa.Column("ledger_entry_id", UUID(as_uuid=True), nullable=True),
    )
    _add_column_if_missing(
        "store_opening_reconciliations",
        sa.Column(
            "ledger_sync_status",
            sa.String(24),
            nullable=False,
            server_default="not_posted",
        ),
    )

    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("store_supplier_invoice_settlements"):
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
    if "ix_store_supplier_invoice_settlements_invoice_id" not in _existing_indexes(
        "store_supplier_invoice_settlements"
    ):
        op.create_index(
            "ix_store_supplier_invoice_settlements_invoice_id",
            "store_supplier_invoice_settlements",
            ["invoice_id"],
        )

    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("store_trade_receivable_payments"):
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
    if "ix_store_trade_receivable_payments_receivable_id" not in _existing_indexes(
        "store_trade_receivable_payments"
    ):
        op.create_index(
            "ix_store_trade_receivable_payments_receivable_id",
            "store_trade_receivable_payments",
            ["receivable_id"],
        )


def downgrade():
    if "ix_store_trade_receivable_payments_receivable_id" in _existing_indexes(
        "store_trade_receivable_payments"
    ):
        op.drop_index(
            "ix_store_trade_receivable_payments_receivable_id",
            table_name="store_trade_receivable_payments",
        )
    if sa.inspect(op.get_bind()).has_table("store_trade_receivable_payments"):
        op.drop_table("store_trade_receivable_payments")
    if "ix_store_supplier_invoice_settlements_invoice_id" in _existing_indexes(
        "store_supplier_invoice_settlements"
    ):
        op.drop_index(
            "ix_store_supplier_invoice_settlements_invoice_id",
            table_name="store_supplier_invoice_settlements",
        )
    if sa.inspect(op.get_bind()).has_table("store_supplier_invoice_settlements"):
        op.drop_table("store_supplier_invoice_settlements")

    _drop_column_if_exists("store_opening_reconciliations", "ledger_sync_status")
    _drop_column_if_exists("store_opening_reconciliations", "ledger_entry_id")
    _drop_column_if_exists("store_opening_reconciliations", "evidence_media_id")

    _drop_column_if_exists("store_trade_receivables", "ledger_sync_status")
    _drop_column_if_exists("store_trade_receivables", "ledger_entry_id")
    _drop_column_if_exists("store_trade_receivables", "settled_at")
    _drop_column_if_exists("store_trade_receivables", "approved_at")

    _drop_column_if_exists("store_supplier_invoices", "evidence_media_id")

    _drop_column_if_exists("store_goods_receipts", "ledger_sync_status")
    _drop_column_if_exists("store_goods_receipts", "ledger_entry_id")
    _drop_column_if_exists("store_goods_receipts", "evidence_media_id")
