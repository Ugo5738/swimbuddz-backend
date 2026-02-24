"""Enums for the Wallet Service models."""

import enum


def enum_values(enum_cls):
    """Return persistent DB values for SAEnum mappings."""
    return [member.value for member in enum_cls]


class WalletStatus(str, enum.Enum):
    ACTIVE = "active"
    FROZEN = "frozen"
    SUSPENDED = "suspended"
    CLOSED = "closed"


class WalletTier(str, enum.Enum):
    STANDARD = "standard"
    PREMIUM = "premium"
    VIP = "vip"


class TransactionType(str, enum.Enum):
    TOPUP = "topup"
    PURCHASE = "purchase"
    REFUND = "refund"
    WELCOME_BONUS = "welcome_bonus"
    PROMOTIONAL_CREDIT = "promotional_credit"
    REFERRAL_CREDIT = "referral_credit"
    ADMIN_ADJUSTMENT = "admin_adjustment"
    TRANSFER_IN = "transfer_in"
    TRANSFER_OUT = "transfer_out"
    PENALTY = "penalty"
    REWARD = "reward"
    EXPIRY = "expiry"


class TransactionDirection(str, enum.Enum):
    CREDIT = "credit"
    DEBIT = "debit"


class TransactionStatus(str, enum.Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    REVERSED = "reversed"


class TopupStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"


class PaymentMethod(str, enum.Enum):
    PAYSTACK = "paystack"
    BANK_TRANSFER = "bank_transfer"
    ADMIN_GRANT = "admin_grant"


class GrantType(str, enum.Enum):
    WELCOME_BONUS = "welcome_bonus"
    REFERRAL_REWARD = "referral_reward"
    LOYALTY_REWARD = "loyalty_reward"
    CAMPAIGN = "campaign"
    COMPENSATION = "compensation"
    ADMIN_MANUAL = "admin_manual"
    SCHOLARSHIP = "scholarship"  # Academy scholarship — reduces installment obligation
    DISCOUNT = "discount"  # Admin-applied fee discount deposited as Bubbles


class ReferralStatus(str, enum.Enum):
    PENDING = "pending"
    QUALIFIED = "qualified"
    REWARDED = "rewarded"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class AuditAction(str, enum.Enum):
    FREEZE = "freeze"
    UNFREEZE = "unfreeze"
    SUSPEND = "suspend"
    CLOSE = "close"
    ADMIN_CREDIT = "admin_credit"
    ADMIN_DEBIT = "admin_debit"
    TIER_CHANGE = "tier_change"
    LIMIT_CHANGE = "limit_change"
