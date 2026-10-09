"""Adopt previously settled Academy payments into shared receipts.

Revision ID: a20261009retro
Revises: a20261008rcpt
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "a20261009retro"
down_revision = "a20261008rcpt"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "academy_bank_receipts",
        sa.Column(
            "preexisting_paid_kobo", sa.BigInteger(), nullable=False, server_default="0"
        ),
    )
    op.add_column(
        "academy_bank_receipts",
        sa.Column("remainder_payment_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_academy_receipt_remainder_payment",
        "academy_bank_receipts",
        "payments",
        ["remainder_payment_id"],
        ["id"],
    )
    op.create_unique_constraint(
        "uq_academy_receipt_remainder_payment",
        "academy_bank_receipts",
        ["remainder_payment_id"],
    )
    op.create_check_constraint(
        "ck_academy_receipt_preexisting_range",
        "academy_bank_receipts",
        "preexisting_paid_kobo >= 0 AND preexisting_paid_kobo <= amount_kobo",
    )


def downgrade():
    op.drop_constraint("ck_academy_receipt_preexisting_range", "academy_bank_receipts")
    op.drop_constraint("uq_academy_receipt_remainder_payment", "academy_bank_receipts")
    op.drop_constraint(
        "fk_academy_receipt_remainder_payment",
        "academy_bank_receipts",
        type_="foreignkey",
    )
    op.drop_column("academy_bank_receipts", "remainder_payment_id")
    op.drop_column("academy_bank_receipts", "preexisting_paid_kobo")
