"""Enum definitions for payments service models."""

import enum


def enum_values(enum_cls):
    """Return persistent DB values for SAEnum mappings."""
    return [member.value for member in enum_cls]


class PaymentStatus(str, enum.Enum):
    PENDING = "pending"
    PENDING_REVIEW = "pending_review"
    PAID = "paid"
    WAIVED = "waived"
    FAILED = "failed"


class PaymentMethod(str, enum.Enum):
    PAYSTACK = "paystack"
    MANUAL_TRANSFER = "manual_transfer"


class PaymentPurpose(str, enum.Enum):
    COMMUNITY = "community"
    CLUB = "club"
    CLUB_BUNDLE = "club_bundle"
    ACADEMY_COHORT = "academy_cohort"
    SESSION_FEE = "session_fee"
    STORE_ORDER = "store_order"
    WALLET_TOPUP = "wallet_topup"


class DiscountType(str, enum.Enum):
    PERCENTAGE = "percentage"
    FIXED = "fixed"


class PayoutStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    PROCESSING = "processing"
    PAID = "paid"
    FAILED = "failed"


class PayoutMethod(str, enum.Enum):
    PAYSTACK_TRANSFER = "paystack_transfer"
    BANK_TRANSFER = "bank_transfer"
    OTHER = "other"
