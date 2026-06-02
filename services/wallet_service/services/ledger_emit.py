"""Emit a wallet transaction's Naira-side journal entry to the ledger (design §8.2).

Best-effort: a ledger failure is logged and NEVER affects the wallet operation
(the WalletTransaction is already committed). [Follow-up: a dead-letter table for
automated replay, like payments_service.] Bubble = NAIRA_PER_BUBBLE kobo.

Liability split (§19-B): grant-funded Bubbles live in `bubbles_liability_promo`,
purchased Bubbles in `bubbles_liability`. Spends draw promo-first — wallet_ops
records the split on the txn metadata (`promo_bubbles` / `purchased_bubbles`).
Grant-funding credits (`reference_type == "grant"`: WELCOME_BONUS +
PROMOTIONAL_CREDIT) credit the promo liability; other credits the purchased one.
Topups are skipped (payments_service already posts them).
"""

from __future__ import annotations

from typing import Optional

from libs.common.config import get_settings
from libs.common.ledger_client import post_journal_entry
from libs.common.logging import get_logger
from services.wallet_service.models import (
    TransactionType,
    WalletLedgerPostFailure,
    WalletTransaction,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

NAIRA_PER_BUBBLE = 100

# A PURCHASE spend's credit account + reporting domain, by (service_source,
# reference_type), with a fallback on service_source alone.
SPEND_CREDIT_BY_SOURCE_REF: dict[tuple[str, str], tuple[str, str]] = {
    ("attendance", "session"): ("revenue_club_session", "club"),
    ("payments_service", "session_fee"): ("revenue_club_session", "club"),
    ("store", "order"): ("revenue_store", "store"),
    ("events", "event"): ("revenue_events", "events"),
    ("transport", "ride_booking"): ("revenue_transport", "transport"),
    ("academy", "enrollment"): ("deferred_revenue_academy", "academy"),
}
SPEND_CREDIT_BY_SOURCE: dict[str, tuple[str, str]] = {
    "attendance": ("revenue_club_session", "club"),
    "store": ("revenue_store", "store"),
    "events": ("revenue_events", "events"),
    "transport": ("revenue_transport", "transport"),
    "academy": ("deferred_revenue_academy", "academy"),
}


def _kobo(bubbles: int) -> int:
    return bubbles * NAIRA_PER_BUBBLE


def _line(account_ref: str, *, debit: int = 0, credit: int = 0, **extra) -> dict:
    line = {
        "account_ref": account_ref,
        "debit": debit,
        "credit": credit,
        "currency": "NGN",
    }
    line.update({k: v for k, v in extra.items() if v is not None})
    return line


def build_wallet_post_kwargs(
    txn: WalletTransaction, member_ref: Optional[str]
) -> Optional[dict]:
    """Build post_journal_entry kwargs for a wallet txn, or None to skip."""
    t = txn.transaction_type
    amt = _kobo(txn.amount)
    meta = txn.txn_metadata or {}
    is_grant = txn.reference_type == "grant"
    lines: Optional[list[dict]] = None

    if t == TransactionType.PURCHASE:
        cred = SPEND_CREDIT_BY_SOURCE_REF.get(
            (txn.service_source or "", txn.reference_type or "")
        ) or SPEND_CREDIT_BY_SOURCE.get(txn.service_source or "")
        if cred is None:
            # A new service spending Bubbles with an unrecognised source would
            # otherwise silently drop revenue (this emitter is log-only, no
            # dead-letter). Surface it loudly so the mapping gets added.
            logger.warning(
                "wallet ledger emit: unmapped PURCHASE source=%s ref_type=%s "
                "(txn %s) — revenue NOT posted; add it to "
                "SPEND_CREDIT_BY_SOURCE[_REF]",
                txn.service_source,
                txn.reference_type,
                txn.id,
            )
            return None  # unmapped spend source
        credit_ref, domain = cred
        promo = int(meta.get("promo_bubbles", 0))
        purchased = int(meta.get("purchased_bubbles", txn.amount))
        lines = []
        if promo > 0:
            lines.append(
                _line(
                    "bubbles_liability_promo", debit=_kobo(promo), member_ref=member_ref
                )
            )
        if purchased > 0:
            lines.append(
                _line(
                    "bubbles_liability", debit=_kobo(purchased), member_ref=member_ref
                )
            )
        lines.append(
            _line(
                credit_ref,
                credit=amt,
                member_ref=member_ref,
                dimension_1=domain,
                external_ref=txn.reference_id,
            )
        )
    elif t == TransactionType.PENALTY:
        lines = [
            _line("bubbles_liability", debit=amt, member_ref=member_ref),
            _line(
                "revenue_penalty", credit=amt, member_ref=member_ref, dimension_1="club"
            ),
        ]
    elif t == TransactionType.EXPIRY:
        lines = [
            _line("bubbles_liability_promo", debit=amt, member_ref=member_ref),
            _line("revenue_bubbles_breakage", credit=amt, member_ref=member_ref),
        ]
    elif is_grant:  # WELCOME_BONUS / PROMOTIONAL_CREDIT — promo liability
        lines = [
            _line("expense_marketing", debit=amt, member_ref=member_ref),
            _line("bubbles_liability_promo", credit=amt, member_ref=member_ref),
        ]
    elif t == TransactionType.REFUND:
        lines = [
            _line("refunds_payable", debit=amt, member_ref=member_ref),
            _line("bubbles_liability", credit=amt, member_ref=member_ref),
        ]
    elif t in (TransactionType.REWARD, TransactionType.REFERRAL_CREDIT):
        lines = [
            _line("expense_marketing", debit=amt, member_ref=member_ref),
            _line("bubbles_liability", credit=amt, member_ref=member_ref),
        ]
    else:
        # topup (payments posts it), transfers, admin_adjustment — skip in v1.
        return None

    settings = get_settings()
    return {
        "entry_date": txn.created_at.date().isoformat(),
        "description": f"Wallet {t.value} — {txn.amount} 🫧",
        "source_service": "wallet",
        "source_type": t.value,
        "source_id": str(txn.id),
        "org_id": settings.LEDGER_DEFAULT_ORG_ID or None,
        "metadata": {"wallet_txn": str(txn.id), "type": t.value},
        "lines": lines,
    }


async def emit_wallet_txn_to_ledger(
    db: AsyncSession, txn: WalletTransaction, member_ref: Optional[str]
) -> None:
    """Post a wallet txn's journal entry to the ledger. NEVER raises.

    Idempotent at the ledger (key wallet:<type>:<txn.id>). On failure the intended
    entry is parked in wallet_ledger_post_failures for replay
    (scripts/ledger/replay_ledger_failures.py) — it never affects the (already
    committed) wallet op.
    """
    kwargs = build_wallet_post_kwargs(txn, member_ref)
    if kwargs is None:
        return
    try:
        await post_journal_entry(calling_service="wallet", **kwargs)
    except Exception as exc:  # noqa: BLE001 — must not affect the wallet op
        logger.warning(
            "wallet ledger emit failed for txn %s (%s); dead-lettering: %s",
            txn.id,
            txn.transaction_type.value,
            exc,
            exc_info=True,
        )
        key = (
            f"{kwargs['source_service']}:{kwargs['source_type']}:"
            f"{kwargs['source_id']}"
        )
        await _dead_letter(db, key, str(kwargs["source_id"]), kwargs, str(exc))


async def _dead_letter(
    db: AsyncSession,
    idempotency_key: str,
    source_reference: str,
    payload: dict,
    error: str,
) -> None:
    """Upsert a dead-letter row for replay. Best-effort — swallows its own errors
    so a logging-table hiccup can't break the (committed) wallet op."""
    try:
        existing = (
            await db.execute(
                select(WalletLedgerPostFailure).where(
                    WalletLedgerPostFailure.idempotency_key == idempotency_key
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            db.add(
                WalletLedgerPostFailure(
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
            "Failed to write WalletLedgerPostFailure for %s",
            source_reference,
            exc_info=True,
        )
        await db.rollback()
