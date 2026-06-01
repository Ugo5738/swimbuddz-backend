"""Emit a payment's cash-in journal entry to ledger_service, with a dead-letter.

Called from _mark_paid_and_apply after a payment is durably PAID. Maps each
PaymentPurpose to its credit account (design doc §8.1); the debit is the PSP's
clearing account. `post_journal_entry` RAISES on failure — we catch it, park the
intended entry in `ledger_post_failures`, and never let a ledger hiccup affect
the payment. The ledger's idempotency_key (payments:payment_paid:<ref>) makes
the eventual replay safe.
"""

from __future__ import annotations

from datetime import date

from libs.common.config import get_settings
from libs.common.datetime_utils import utc_now
from libs.common.ledger_client import post_journal_entry
from libs.common.logging import get_logger
from services.payments_service.models import Payment, PaymentPurpose
from services.payments_service.models.ledger_failure import LedgerPostFailure
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

SOURCE_SERVICE = "payments"
SOURCE_TYPE = "payment_paid"

# Design §8.1 — credit account per purpose (debit is always a clearing account).
PURPOSE_TO_CREDIT_REF: dict[PaymentPurpose, str] = {
    PaymentPurpose.COMMUNITY: "deferred_revenue_community",
    PaymentPurpose.CLUB: "deferred_revenue_club",
    PaymentPurpose.CLUB_BUNDLE: "deferred_revenue_club",
    PaymentPurpose.ACADEMY_COHORT: "deferred_revenue_academy",
    PaymentPurpose.SESSION_FEE: "revenue_club_session",
    PaymentPurpose.SESSION_BUNDLE: "deferred_revenue_session_bundle",
    # Single-session pre-booking: recognised at payment in Phase 1 (like a fee).
    PaymentPurpose.SESSION_BOOKING: "revenue_club_session",
    PaymentPurpose.STORE_ORDER: "revenue_store",
    PaymentPurpose.WALLET_TOPUP: "bubbles_liability",
    PaymentPurpose.RIDE_SHARE: "revenue_transport",
}

# Optional reporting dimension (dimension_1) per purpose.
PURPOSE_TO_DOMAIN: dict[PaymentPurpose, str] = {
    PaymentPurpose.COMMUNITY: "community",
    PaymentPurpose.CLUB: "club",
    PaymentPurpose.CLUB_BUNDLE: "club",
    PaymentPurpose.ACADEMY_COHORT: "academy",
    PaymentPurpose.SESSION_FEE: "club",
    PaymentPurpose.SESSION_BUNDLE: "club",
    PaymentPurpose.SESSION_BOOKING: "club",
    PaymentPurpose.STORE_ORDER: "store",
    PaymentPurpose.WALLET_TOPUP: "wallet",
    PaymentPurpose.RIDE_SHARE: "transport",
}

PROVIDER_TO_DEBIT_REF = {
    "paystack": "paystack_clearing",
    "flutterwave": "flutterwave_clearing",
}


def to_kobo(amount: float) -> int:
    """Convert Float NGN to integer kobo (round-half-even).

    Payment.amount is Float today (design §9 tech debt). round() is banker's
    rounding; the conversion is centralised here as the single drift point.
    """
    return int(round(amount * 100))


def build_post_kwargs(payment: Payment) -> dict | None:
    """Build post_journal_entry kwargs for a paid payment, or None if unmapped."""
    credit_ref = PURPOSE_TO_CREDIT_REF.get(payment.purpose)
    if credit_ref is None:
        return None
    debit_ref = PROVIDER_TO_DEBIT_REF.get(
        (payment.provider or "").lower(), "bank_operating_ngn"
    )
    amount = to_kobo(payment.amount)
    currency = payment.currency or "NGN"
    entry_date: date = (payment.paid_at or utc_now()).date()
    settings = get_settings()
    return {
        "entry_date": entry_date.isoformat(),
        "description": f"Payment {payment.reference} — {payment.purpose.value}",
        "source_service": SOURCE_SERVICE,
        "source_type": SOURCE_TYPE,
        "source_id": payment.reference,
        "org_id": settings.LEDGER_DEFAULT_ORG_ID or None,
        "metadata": {
            "payment_reference": payment.reference,
            "purpose": payment.purpose.value,
        },
        "lines": [
            {
                "account_ref": debit_ref,
                "debit": amount,
                "currency": currency,
                "external_ref": payment.reference,
                "member_ref": payment.member_auth_id,
            },
            {
                "account_ref": credit_ref,
                "credit": amount,
                "currency": currency,
                "external_ref": payment.reference,
                "member_ref": payment.member_auth_id,
                "dimension_1": PURPOSE_TO_DOMAIN.get(payment.purpose),
                "description": f"{payment.purpose.value} payment",
            },
        ],
    }


async def emit_payment_to_ledger(db: AsyncSession, payment: Payment) -> None:
    """Post a paid payment's journal entry to the ledger. NEVER raises.

    On failure the intended entry is parked in ledger_post_failures for replay.
    """
    kwargs = build_post_kwargs(payment)
    if kwargs is None:
        logger.warning(
            "No ledger mapping for purpose=%s (payment %s); skipping emit",
            payment.purpose,
            payment.reference,
        )
        return

    idempotency_key = f"{SOURCE_SERVICE}:{SOURCE_TYPE}:{payment.reference}"
    try:
        await post_journal_entry(calling_service=SOURCE_SERVICE, **kwargs)
    except Exception as exc:  # noqa: BLE001 — must not affect the payment
        logger.warning(
            "Ledger post failed for payment %s; dead-lettering: %s",
            payment.reference,
            exc,
            exc_info=True,
        )
        await _dead_letter(db, idempotency_key, payment.reference, kwargs, str(exc))


async def _dead_letter(
    db: AsyncSession,
    idempotency_key: str,
    source_reference: str | None,
    payload: dict,
    error: str,
) -> None:
    """Upsert a dead-letter row. Best-effort — swallows its own errors."""
    try:
        existing = (
            await db.execute(
                select(LedgerPostFailure).where(
                    LedgerPostFailure.idempotency_key == idempotency_key
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            db.add(
                LedgerPostFailure(
                    idempotency_key=idempotency_key,
                    source_reference=source_reference,
                    payload=payload,
                    attempts=1,
                    last_error=error,
                    status="pending",
                )
            )
        else:
            existing.attempts += 1
            existing.last_error = error
            existing.status = "pending"
        await db.commit()
    except Exception:
        logger.error(
            "Failed to write LedgerPostFailure for %s",
            source_reference,
            exc_info=True,
        )
        await db.rollback()
