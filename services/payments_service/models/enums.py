"""Enum definitions for payments service models."""

import enum

# PaymentStatus is the canonical cross-service payment lifecycle enum and
# lives in libs/common/enums.py. Re-exported here so existing imports like
# ``from services.payments_service.models.enums import PaymentStatus`` keep
# working unchanged.
from libs.common.enums import PaymentStatus  # noqa: F401


def enum_values(enum_cls):
    """Return persistent DB values for SAEnum mappings."""
    return [member.value for member in enum_cls]


class PaymentPurpose(str, enum.Enum):
    COMMUNITY = "community"
    CLUB = "club"
    CLUB_BUNDLE = "club_bundle"
    ACADEMY_COHORT = "academy_cohort"
    SESSION_FEE = "session_fee"
    SESSION_BUNDLE = "session_bundle"
    STORE_ORDER = "store_order"
    WALLET_TOPUP = "wallet_topup"
    RIDE_SHARE = "ride_share"
    # A1 Phase 3.3 — Paystack pre-booking. The entitlement handler calls
    # sessions_service POST /internal/sessions/bookings/{id}/confirm to
    # flip the PENDING SessionBooking to CONFIRMED once payment clears.
    SESSION_BOOKING = "session_booking"
    # Stroke Lab founding-member lifetime pre-sale (₦20k, capped at 100).
    # ai_service initializes + verifies through payments_service so the
    # revenue lands in the unified Payment ledger like every other purpose.
    STROKELAB_FOUNDING = "strokelab_founding"


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


class MakeupStatus(str, enum.Enum):
    """Lifecycle of a make-up obligation owed to a student."""

    PENDING = "pending"  # Obligation created, no make-up scheduled yet
    SCHEDULED = "scheduled"  # Coach has scheduled a make-up session
    COMPLETED = "completed"  # Make-up session was held and student attended
    EXPIRED = "expired"  # Cohort ended before make-up was delivered
    CANCELLED = "cancelled"  # Admin cancelled the obligation


class MakeupReason(str, enum.Enum):
    """Why a make-up is owed to a student."""

    LATE_JOIN = "late_join"  # Student enrolled after sessions began
    EXCUSED_ABSENCE = "excused_absence"  # Coach marked EXCUSED for a session
    SESSION_CANCELLED = "session_cancelled"  # A scheduled session was cancelled


class RecurringPayoutStatus(str, enum.Enum):
    """Lifecycle of a recurring payout configuration."""

    ACTIVE = "active"
    PAUSED = "paused"  # Admin temporarily paused; no new payouts created
    COMPLETED = "completed"  # All blocks paid out
    CANCELLED = "cancelled"  # Admin cancelled mid-cohort
