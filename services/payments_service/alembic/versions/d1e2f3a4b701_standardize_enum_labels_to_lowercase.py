"""standardize_enum_labels_to_lowercase

Revision ID: d1e2f3a4b701
Revises: 5f1a9b0e7c2d
Create Date: 2026-02-22 05:43:00.000000
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "d1e2f3a4b701"
down_revision = "5f1a9b0e7c2d"
branch_labels = None
depends_on = None


ENUM_VALUE_MAP = {
    "payment_purpose_enum": [
        ("COMMUNITY", "community"),
        ("CLUB", "club"),
        ("CLUB_BUNDLE", "club_bundle"),
        ("ACADEMY_COHORT", "academy_cohort"),
        ("SESSION_FEE", "session_fee"),
        ("STORE_ORDER", "store_order"),
        ("WALLET_TOPUP", "wallet_topup"),
    ],
    "payment_status_enum": [
        ("PENDING", "pending"),
        ("PENDING_REVIEW", "pending_review"),
        ("PAID", "paid"),
        ("WAIVED", "waived"),
        ("FAILED", "failed"),
    ],
    "discount_type_enum": [("PERCENTAGE", "percentage"), ("FIXED", "fixed")],
    "payout_status_enum": [
        ("PENDING", "pending"),
        ("APPROVED", "approved"),
        ("PROCESSING", "processing"),
        ("PAID", "paid"),
        ("FAILED", "failed"),
    ],
    "payout_method_enum": [
        ("PAYSTACK_TRANSFER", "paystack_transfer"),
        ("BANK_TRANSFER", "bank_transfer"),
        ("OTHER", "other"),
    ],
}

COLUMN_ENUM_MAP = [
    ("payments", "purpose", "payment_purpose_enum"),
    ("payments", "status", "payment_status_enum"),
    ("discounts", "discount_type", "discount_type_enum"),
    ("coach_payouts", "status", "payout_status_enum"),
    ("coach_payouts", "payout_method", "payout_method_enum"),
]


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for enum_type, pairs in ENUM_VALUE_MAP.items():
            for _, new in pairs:
                op.execute(f"ALTER TYPE {enum_type} ADD VALUE IF NOT EXISTS '{new}'")

    for table_name, column_name, enum_type in COLUMN_ENUM_MAP:
        for old, new in ENUM_VALUE_MAP[enum_type]:
            op.execute(
                f"UPDATE {table_name} SET {column_name} = '{new}' WHERE {column_name}::text = '{old}'"
            )


def downgrade() -> None:
    # Intentionally no-op: enum label removal is destructive.
    pass
