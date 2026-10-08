"""Academy shared bank receipt allocation ledger.

Revision ID: a20261008rcpt
Revises: pa20261008b1
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "a20261008rcpt"
down_revision = "pa20261008b1"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("academy_bank_receipts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("payment_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("payments.id"), nullable=False, unique=True),
        sa.Column("external_reference", sa.String(160), nullable=False),
        sa.Column("amount_kobo", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False),
        sa.Column("verification_note", sa.Text(), nullable=False),
        sa.Column("verified_by_auth_id", sa.String(160), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("amount_kobo > 0", name="ck_academy_receipt_positive"),
        sa.UniqueConstraint("external_reference", name="uq_academy_receipt_external_reference"))
    op.create_table("academy_receipt_allocations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("receipt_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("academy_bank_receipts.id"), nullable=False),
        sa.Column("member_auth_id", sa.String(160), nullable=False),
        sa.Column("enrollment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("amount_kobo", sa.BigInteger(), nullable=False),
        sa.Column("idempotency_key", sa.String(160), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("created_by_auth_id", sa.String(160), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("voided_by_auth_id", sa.String(160), nullable=True),
        sa.Column("void_reason", sa.Text(), nullable=True),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("amount_kobo > 0", name="ck_academy_receipt_allocation_positive"),
        sa.UniqueConstraint("receipt_id", "idempotency_key", name="uq_academy_receipt_allocation_idempotency"))
    op.create_index("ix_academy_receipt_allocations_receipt_id", "academy_receipt_allocations", ["receipt_id"])
    op.create_index("ix_academy_receipt_allocations_member_auth_id", "academy_receipt_allocations", ["member_auth_id"])
    op.create_index("ix_academy_receipt_allocation_enrollment_state", "academy_receipt_allocations", ["enrollment_id", "state"])

def downgrade():
    op.drop_index("ix_academy_receipt_allocation_enrollment_state", table_name="academy_receipt_allocations")
    op.drop_index("ix_academy_receipt_allocations_member_auth_id", table_name="academy_receipt_allocations")
    op.drop_index("ix_academy_receipt_allocations_receipt_id", table_name="academy_receipt_allocations")
    op.drop_table("academy_receipt_allocations")
    op.drop_table("academy_bank_receipts")
