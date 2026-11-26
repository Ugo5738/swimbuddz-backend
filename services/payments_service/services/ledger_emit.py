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

from dateutil.relativedelta import relativedelta
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
    PaymentPurpose.GUEST_PASS: "revenue_club_session",
    PaymentPurpose.COMMUNITY_EXPERIENCE: "deferred_revenue_community",
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
    PaymentPurpose.GUEST_PASS: "club",
    PaymentPurpose.COMMUNITY_EXPERIENCE: "community",
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


def _payment_revenue_mapping(payment: Payment) -> tuple[str | None, str | None]:
    """Resolve ledger account/domain, refining session cash by session type.

    Older rows without a session_type snapshot retain the historical Club
    fallback. New session/guest settlements carry the type explicitly so a
    Community Swim does not appear as Club revenue merely because it used the
    generic session checkout machinery.
    """
    credit_ref = PURPOSE_TO_CREDIT_REF.get(payment.purpose)
    domain = PURPOSE_TO_DOMAIN.get(payment.purpose)
    if payment.purpose in {
        PaymentPurpose.SESSION_FEE,
        PaymentPurpose.SESSION_BOOKING,
        PaymentPurpose.GUEST_PASS,
    } or (
        payment.purpose == PaymentPurpose.SESSION_BUNDLE
        and "admin_session_settlement" in (payment.payment_metadata or {})
    ):
        session_type = str(
            (payment.payment_metadata or {}).get("session_type") or ""
        ).lower()
        by_type = {
            "club": ("revenue_club_session", "club"),
            "community": ("revenue_community", "community"),
            "event": ("revenue_events", "events"),
            "cohort_class": ("revenue_academy", "academy"),
        }
        if session_type in by_type:
            credit_ref, domain = by_type[session_type]
    return credit_ref, domain


def build_post_kwargs(payment: Payment) -> dict | None:
    """Build post_journal_entry kwargs for a paid payment, or None if unmapped."""
    credit_ref, domain = _payment_revenue_mapping(payment)
    if credit_ref is None:
        return None
    debit_ref = PROVIDER_TO_DEBIT_REF.get(
        (payment.provider or "").lower(), "bank_operating_ngn"
    )
    amount = to_kobo(payment.amount)
    if amount <= 0:
        # Bubbles settlement has its own wallet journal; free/discount credit is
        # not a cash receipt and must not create zero-value accounting entries.
        return None
    currency = payment.currency or "NGN"
    entry_date: date = (payment.paid_at or utc_now()).date()
    settings = get_settings()
    metadata: dict = {
        "payment_reference": payment.reference,
        "purpose": payment.purpose.value,
    }
    # Club: pass the member's selected term so revenue recognition spans the
    # exact membership window. payment_metadata["months"] (3/6/12) is set at
    # intent creation from the quarterly/6mo/annual choice; convert months->days
    # the same way members_service computes club_paid_until (via relativedelta).
    if credit_ref == "deferred_revenue_club":
        months = int((payment.payment_metadata or {}).get("months") or 0)
        if months > 0:
            metadata["recognition_days"] = (
                entry_date + relativedelta(months=months) - entry_date
            ).days
    return {
        "entry_date": entry_date.isoformat(),
        "description": f"Payment {payment.reference} — {payment.purpose.value}",
        "source_service": SOURCE_SERVICE,
        "source_type": SOURCE_TYPE,
        "source_id": payment.reference,
        "org_id": settings.LEDGER_DEFAULT_ORG_ID or None,
        "metadata": metadata,
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
                "dimension_1": domain,
                "description": f"{payment.purpose.value} payment",
            },
        ],
    }


async def emit_payment_to_ledger(db: AsyncSession, payment: Payment) -> None:
    """Post a paid payment's journal entry to the ledger. NEVER raises.

    On failure the intended entry is parked in ledger_post_failures for replay.
    """
    if to_kobo(payment.amount) <= 0:
        return
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


# ---------------------------------------------------------------------------
# Cash-out emitters (design §8.1) — refunds + coach payouts. All best-effort:
# never raise, dead-letter on failure (same pattern as the cash-in emitter).
# ---------------------------------------------------------------------------


async def _post_or_dead_letter(
    db: AsyncSession, idempotency_key: str, source_reference: str, kwargs: dict
) -> None:
    try:
        await post_journal_entry(calling_service=SOURCE_SERVICE, **kwargs)
    except Exception as exc:  # noqa: BLE001 — must not affect the source op
        logger.warning(
            "Ledger post failed (%s); dead-lettering: %s",
            idempotency_key,
            exc,
            exc_info=True,
        )
        await _dead_letter(db, idempotency_key, source_reference, kwargs, str(exc))


async def emit_refund_disbursed_to_ledger(
    db: AsyncSession, payment: Payment, refund_kobo: int, enrollment_id: str
) -> None:
    """Cash refund out: DR the account credited at cash-in / CR bank.

    Reverses the original purpose's revenue/deferred account (PURPOSE_TO_CREDIT_REF).
    Idempotent per (payment.reference, enrollment_id).
    """
    credit_ref = PURPOSE_TO_CREDIT_REF.get(payment.purpose)
    if credit_ref is None or refund_kobo <= 0:
        return
    settings = get_settings()
    source_id = f"{payment.reference}:{enrollment_id}"
    currency = payment.currency or "NGN"
    kwargs = {
        "entry_date": utc_now().date().isoformat(),
        "description": f"Refund {payment.reference} — {payment.purpose.value}",
        "source_service": SOURCE_SERVICE,
        "source_type": "refund_disbursed",
        "source_id": source_id,
        "org_id": settings.LEDGER_DEFAULT_ORG_ID or None,
        "metadata": {
            "payment_reference": payment.reference,
            "enrollment_id": enrollment_id,
        },
        "lines": [
            {
                "account_ref": credit_ref,
                "debit": refund_kobo,
                "currency": currency,
                "member_ref": payment.member_auth_id,
                "dimension_1": PURPOSE_TO_DOMAIN.get(payment.purpose),
                "external_ref": payment.reference,
            },
            {
                "account_ref": "bank_operating_ngn",
                "credit": refund_kobo,
                "currency": currency,
                "member_ref": payment.member_auth_id,
                "external_ref": payment.reference,
            },
        ],
    }
    await _post_or_dead_letter(
        db, f"{SOURCE_SERVICE}:refund_disbursed:{source_id}", payment.reference, kwargs
    )


async def emit_refund_approved_to_ledger(db: AsyncSession, refund) -> bool:
    """Accrue an approved cash refund (ADR-024):

        DR <purpose revenue/deferred account>   (cash_refund_kobo)
        CR refunds_payable (2130)               (cash_refund_kobo)

    Idempotent per ``refund.refund_reference``. Returns True if posted, False if
    dead-lettered or skipped.
    """
    amount = int(getattr(refund, "cash_refund_kobo", 0) or 0)
    if amount <= 0:
        return True
    try:
        purpose_enum = PaymentPurpose(str(refund.purpose).lower())
    except ValueError:
        purpose_enum = None
    debit_ref = (
        PURPOSE_TO_CREDIT_REF.get(purpose_enum) if purpose_enum is not None else None
    )
    if debit_ref is None:
        debit_ref = "revenue_store"
    domain = (
        PURPOSE_TO_DOMAIN.get(purpose_enum)
        if purpose_enum is not None
        else (refund.source_service or "store")
    )
    settings = get_settings()
    currency = refund.currency or "NGN"
    ref = refund.refund_reference
    kwargs = {
        "entry_date": (getattr(refund, "approved_at", None) or utc_now())
        .date()
        .isoformat(),
        "description": f"Refund approved {ref} — {refund.purpose}",
        "source_service": SOURCE_SERVICE,
        "source_type": "refund_approved",
        "source_id": ref,
        "org_id": settings.LEDGER_DEFAULT_ORG_ID or None,
        "metadata": {
            "refund_reference": ref,
            "payment_reference": refund.payment_reference,
            "source_service": refund.source_service,
            "source_entity_id": refund.source_entity_id,
        },
        "lines": [
            {
                "account_ref": debit_ref,
                "debit": amount,
                "currency": currency,
                "member_ref": refund.member_auth_id,
                "dimension_1": domain,
                "external_ref": ref,
                "description": f"Refund reversal ({refund.purpose})",
            },
            {
                "account_ref": "refunds_payable",
                "credit": amount,
                "currency": currency,
                "member_ref": refund.member_auth_id,
                "dimension_1": domain,
                "external_ref": ref,
                "description": f"Refunds payable for {ref}",
            },
        ],
    }
    idempotency_key = f"{SOURCE_SERVICE}:refund_approved:{ref}"
    try:
        await post_journal_entry(calling_service=SOURCE_SERVICE, **kwargs)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Ledger refund-approved post failed (%s); dead-lettering: %s",
            idempotency_key,
            exc,
            exc_info=True,
        )
        await _dead_letter(db, idempotency_key, ref, kwargs, str(exc))
        return False


async def emit_refund_completed_to_ledger(db: AsyncSession, refund) -> bool:
    """Clear Refunds Payable on cash refund completion (ADR-024):

        DR refunds_payable (2130)                               (cash_refund_kobo)
        CR paystack_clearing (Paystack) / bank_operating_ngn    (cash_refund_kobo)

    Idempotent per ``refund.refund_reference``. Returns True if posted, False if
    dead-lettered.
    """
    amount = int(getattr(refund, "cash_refund_kobo", 0) or 0)
    if amount <= 0:
        return True
    provider = (getattr(refund, "provider", None) or "").lower()
    credit_ref = "paystack_clearing" if provider == "paystack" else "bank_operating_ngn"
    settings = get_settings()
    currency = refund.currency or "NGN"
    ref = refund.refund_reference
    domain = refund.source_service or "store"
    kwargs = {
        "entry_date": (getattr(refund, "completed_at", None) or utc_now())
        .date()
        .isoformat(),
        "description": f"Refund disbursed {ref} ({provider or 'manual'})",
        "source_service": SOURCE_SERVICE,
        "source_type": "refund_completed",
        "source_id": ref,
        "org_id": settings.LEDGER_DEFAULT_ORG_ID or None,
        "metadata": {
            "refund_reference": ref,
            "payment_reference": refund.payment_reference,
            "provider": provider,
            "provider_refund_id": refund.provider_refund_id,
            "external_bank_reference": refund.external_bank_reference,
        },
        "lines": [
            {
                "account_ref": "refunds_payable",
                "debit": amount,
                "currency": currency,
                "member_ref": refund.member_auth_id,
                "dimension_1": domain,
                "external_ref": ref,
            },
            {
                "account_ref": credit_ref,
                "credit": amount,
                "currency": currency,
                "member_ref": refund.member_auth_id,
                "dimension_1": domain,
                "external_ref": (
                    refund.external_bank_reference or refund.provider_refund_id or ref
                ),
            },
        ],
    }
    idempotency_key = f"{SOURCE_SERVICE}:refund_completed:{ref}"
    try:
        await post_journal_entry(calling_service=SOURCE_SERVICE, **kwargs)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Ledger refund-completed post failed (%s); dead-lettering: %s",
            idempotency_key,
            exc,
            exc_info=True,
        )
        await _dead_letter(db, idempotency_key, ref, kwargs, str(exc))
        return False


async def emit_payout_accrual_to_ledger(db: AsyncSession, payout) -> None:
    """Accrue coach pay at block end: DR cogs_coach_academy / CR coach_payouts_payable.

    Domain is academy today — all CoachPayout earnings are academy_earnings; add a
    cohort/source field to split cogs_coach_club later. Idempotent per payout id.
    """
    amount = int(payout.total_amount or 0)
    if amount <= 0:
        return
    settings = get_settings()
    end = getattr(payout, "period_end", None)
    coach = str(payout.coach_member_id)
    kwargs = {
        "entry_date": (end or utc_now()).date().isoformat(),
        "description": f"Coach payout accrual — {payout.id}",
        "source_service": SOURCE_SERVICE,
        "source_type": "payout_accrual",
        "source_id": str(payout.id),
        "org_id": settings.LEDGER_DEFAULT_ORG_ID or None,
        "metadata": {"payout_id": str(payout.id), "coach": coach},
        "lines": [
            {
                "account_ref": "cogs_coach_academy",
                "debit": amount,
                "currency": payout.currency or "NGN",
                "member_ref": coach,
                "dimension_1": "academy",
            },
            {
                "account_ref": "coach_payouts_payable",
                "credit": amount,
                "currency": payout.currency or "NGN",
                "member_ref": coach,
            },
        ],
    }
    await _post_or_dead_letter(
        db, f"{SOURCE_SERVICE}:payout_accrual:{payout.id}", str(payout.id), kwargs
    )


async def emit_payout_paid_to_ledger(db: AsyncSession, payout) -> None:
    """Pay coach (transfer success): DR coach_payouts_payable / CR bank.

    Clears the payable the accrual booked. Idempotent per payout id.
    """
    amount = int(payout.total_amount or 0)
    if amount <= 0:
        return
    settings = get_settings()
    paid = getattr(payout, "paid_at", None)
    coach = str(payout.coach_member_id)
    kwargs = {
        "entry_date": (paid or utc_now()).date().isoformat(),
        "description": f"Coach payout paid — {payout.id}",
        "source_service": SOURCE_SERVICE,
        "source_type": "payout_paid",
        "source_id": str(payout.id),
        "org_id": settings.LEDGER_DEFAULT_ORG_ID or None,
        "metadata": {"payout_id": str(payout.id), "coach": coach},
        "lines": [
            {
                "account_ref": "coach_payouts_payable",
                "debit": amount,
                "currency": payout.currency or "NGN",
                "member_ref": coach,
            },
            {
                "account_ref": "bank_operating_ngn",
                "credit": amount,
                "currency": payout.currency or "NGN",
                "member_ref": coach,
            },
        ],
    }
    await _post_or_dead_letter(
        db, f"{SOURCE_SERVICE}:payout_paid:{payout.id}", str(payout.id), kwargs
    )


# ---------------------------------------------------------------------------
# Settlement drain (design §11.1, R3) — closes paystack_clearing to the bank.
# ---------------------------------------------------------------------------


async def emit_settlement_to_ledger(db: AsyncSession, settlement) -> bool:
    """Drain clearing for a Paystack settlement (ADR-024 deduction-safe):

        DR bank_operating_ngn (net) + DR expense_psp_fees (fees)
        CR paystack_clearing       (net + fees = gross - deductions)

    When Paystack nets API refunds out of a settlement, ``refund_completed`` has
    already credited ``paystack_clearing`` for each refunded transaction. Using
    ``fees_minor`` strictly for ``expense_psp_fees`` and draining ``net + fees``
    from ``paystack_clearing`` prevents double-counting refunds as PSP fees.
    """
    gross = int(settlement.gross_minor or 0)
    net = int(settlement.net_minor or 0)
    fees_recorded = int(getattr(settlement, "fees_minor", 0) or 0)
    deductions_recorded = int(getattr(settlement, "deductions_minor", 0) or 0)
    if gross <= 0 and net <= 0:
        return True  # nothing to drain

    if fees_recorded > 0 or deductions_recorded > 0:
        fee_expense = max(0, fees_recorded)
        deductions = max(deductions_recorded, max(0, gross - net - fee_expense))
    else:
        # Legacy settlement rows without explicit fees_minor/deductions_minor
        fee_expense = max(0, gross - net)
        deductions = 0

    clearing_drain = net + fee_expense
    if clearing_drain <= 0:
        return True
    currency = settlement.currency or "NGN"
    sid = str(settlement.paystack_settlement_id)
    entry_date: date = settlement.settlement_date or utc_now().date()
    settings = get_settings()

    lines: list[dict] = []
    if net > 0:
        lines.append(
            {
                "account_ref": "bank_operating_ngn",
                "debit": net,
                "currency": currency,
                "external_ref": sid,
            }
        )
    if fee_expense > 0:
        lines.append(
            {
                "account_ref": "expense_psp_fees",
                "debit": fee_expense,
                "currency": currency,
                "external_ref": sid,
            }
        )
    lines.append(
        {
            "account_ref": "paystack_clearing",
            "credit": clearing_drain,
            "currency": currency,
            "external_ref": sid,
        }
    )

    kwargs = {
        "entry_date": entry_date.isoformat(),
        "description": f"Paystack settlement {sid}",
        "source_service": SOURCE_SERVICE,
        "source_type": "settlement",
        "source_id": sid,
        "org_id": settings.LEDGER_DEFAULT_ORG_ID or None,
        "metadata": {
            "paystack_settlement_id": sid,
            "gross": gross,
            "net": net,
            "fees": fee_expense,
            "deductions": deductions,
            "clearing_drain": clearing_drain,
        },
        "lines": lines,
    }
    try:
        await post_journal_entry(calling_service=SOURCE_SERVICE, **kwargs)
        return True
    except Exception as exc:  # noqa: BLE001 — never break the ingest loop
        logger.warning(
            "Ledger settlement post failed (%s); dead-lettering: %s",
            sid,
            exc,
            exc_info=True,
        )
        await _dead_letter(
            db, f"{SOURCE_SERVICE}:settlement:{sid}", sid, kwargs, str(exc)
        )
        return False
