"""External Pool Access partner settlement evidence.

Revision ID: ac20261008e5
Revises: ac20261008d4
"""
from alembic import op
import sqlalchemy as sa

revision = "ac20261008e5"
down_revision = "ac20261008d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pool_access_partner_settlements",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("reconciliation_id", sa.UUID(), sa.ForeignKey("pool_access_reconciliations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("amount_kobo", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("bank_reference", sa.String(160), nullable=False),
        sa.Column("evidence_note", sa.Text(), nullable=False),
        sa.Column("recorded_by", sa.String(255), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("amount_kobo > 0", name="ck_pool_access_partner_settlement_amount"),
        sa.UniqueConstraint("bank_reference", name="uq_pool_access_partner_settlement_bank_ref"),
    )
    op.create_index("ix_pool_access_partner_settlements_reconciliation_id", "pool_access_partner_settlements", ["reconciliation_id"])


def downgrade() -> None:
    op.drop_index("ix_pool_access_partner_settlements_reconciliation_id", table_name="pool_access_partner_settlements")
    op.drop_table("pool_access_partner_settlements")
