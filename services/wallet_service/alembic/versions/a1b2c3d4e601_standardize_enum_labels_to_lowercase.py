"""standardize_enum_labels_to_lowercase

Revision ID: a1b2c3d4e601
Revises: aca6a1c98788
Create Date: 2026-02-22 05:46:00.000000
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "a1b2c3d4e601"
down_revision = "aca6a1c98788"
branch_labels = None
depends_on = None


ENUM_VALUE_MAP = {
    "wallet_status_enum": [
        ("ACTIVE", "active"),
        ("FROZEN", "frozen"),
        ("SUSPENDED", "suspended"),
        ("CLOSED", "closed"),
    ],
    "wallet_tier_enum": [
        ("STANDARD", "standard"),
        ("PREMIUM", "premium"),
        ("VIP", "vip"),
    ],
    "transaction_type_enum": [
        ("TOPUP", "topup"),
        ("PURCHASE", "purchase"),
        ("REFUND", "refund"),
        ("WELCOME_BONUS", "welcome_bonus"),
        ("PROMOTIONAL_CREDIT", "promotional_credit"),
        ("REFERRAL_CREDIT", "referral_credit"),
        ("ADMIN_ADJUSTMENT", "admin_adjustment"),
        ("TRANSFER_IN", "transfer_in"),
        ("TRANSFER_OUT", "transfer_out"),
        ("PENALTY", "penalty"),
        ("REWARD", "reward"),
        ("EXPIRY", "expiry"),
    ],
    "transaction_direction_enum": [
        ("CREDIT", "credit"),
        ("DEBIT", "debit"),
    ],
    "transaction_status_enum": [
        ("PENDING", "pending"),
        ("COMPLETED", "completed"),
        ("FAILED", "failed"),
        ("REVERSED", "reversed"),
    ],
    "topup_payment_method_enum": [
        ("PAYSTACK", "paystack"),
        ("BANK_TRANSFER", "bank_transfer"),
        ("ADMIN_GRANT", "admin_grant"),
    ],
    "topup_status_enum": [
        ("PENDING", "pending"),
        ("PROCESSING", "processing"),
        ("COMPLETED", "completed"),
        ("FAILED", "failed"),
        ("EXPIRED", "expired"),
    ],
    "grant_type_enum": [
        ("WELCOME_BONUS", "welcome_bonus"),
        ("REFERRAL_REWARD", "referral_reward"),
        ("LOYALTY_REWARD", "loyalty_reward"),
        ("CAMPAIGN", "campaign"),
        ("COMPENSATION", "compensation"),
        ("ADMIN_MANUAL", "admin_manual"),
        ("SCHOLARSHIP", "scholarship"),
        ("DISCOUNT", "discount"),
    ],
    "audit_action_enum": [
        ("FREEZE", "freeze"),
        ("UNFREEZE", "unfreeze"),
        ("SUSPEND", "suspend"),
        ("CLOSE", "close"),
        ("ADMIN_CREDIT", "admin_credit"),
        ("ADMIN_DEBIT", "admin_debit"),
        ("TIER_CHANGE", "tier_change"),
        ("LIMIT_CHANGE", "limit_change"),
    ],
    "referral_status_enum": [
        ("PENDING", "pending"),
        ("QUALIFIED", "qualified"),
        ("REWARDED", "rewarded"),
        ("EXPIRED", "expired"),
        ("CANCELLED", "cancelled"),
    ],
}

COLUMN_ENUM_MAP = [
    ("wallets", "status", "wallet_status_enum"),
    ("wallets", "wallet_tier", "wallet_tier_enum"),
    ("wallet_transactions", "transaction_type", "transaction_type_enum"),
    ("wallet_transactions", "direction", "transaction_direction_enum"),
    ("wallet_transactions", "status", "transaction_status_enum"),
    ("wallet_topups", "payment_method", "topup_payment_method_enum"),
    ("wallet_topups", "status", "topup_status_enum"),
    ("promotional_bubble_grants", "grant_type", "grant_type_enum"),
    ("wallet_audit_logs", "action", "audit_action_enum"),
    ("referral_records", "status", "referral_status_enum"),
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
