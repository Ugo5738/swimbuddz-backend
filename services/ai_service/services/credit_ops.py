"""In-service credit ledger for the PUBLIC Stroke Lab analyzer.

Replicates wallet_service's atomic ledger pattern (idempotency pre-check + row
lock + balance snapshots) INSIDE ai_service — service isolation forbids
importing or calling wallet_service. Reference: wallet_ops.debit_wallet. Design:
docs/design/STROKELAB_PUBLIC_ANALYZER_DESIGN.md §6/§7.

TRANSACTION CONTRACT — these helpers operate on the passed AsyncSession and DO
NOT commit. The caller owns the boundary so credit changes commit ATOMICALLY
with what they belong to:
  * submit  — job INSERT + reserve + storage-path in one commit
  * worker  — status flip + consume/refund in one commit
  * webhook — grant/revoke in the webhook handler's commit
The account row is locked (upsert-then-``SELECT ... FOR UPDATE``) before any
read-modify-write, so concurrent ops on the same email serialize; the
``idempotency_key`` + ``gumroad_sale_id`` unique constraints are the backstop.
"""

from __future__ import annotations

import uuid
from typing import Optional

from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from services.ai_service.models import (
    AnalyzerCreditAccount,
    AnalyzerCreditDirection,
    AnalyzerCreditEntryType,
    AnalyzerCreditLedger,
)

logger = get_logger(__name__)

# Gumroad permalink → credits granted per sale.
PERMALINK_CREDITS = {"vrjec": 1, "fgopu": 3, "puxlbz": 10, "arlum": 25}


class NoCreditsError(Exception):
    """No free analysis left and no purchased credits. Router maps this to 402."""


def canonicalize_email(raw: str) -> str:
    """Collapse an email to one free-tier identity: lowercase, strip ``+tag``,
    and drop Gmail dots, so ``me+1@gmail`` / ``m.e@gmail`` are one identity
    (design §6.4). This is the ``analyzer_credit_accounts`` key + the suffix on
    every idempotency key."""
    local, _, domain = (raw or "").strip().lower().partition("@")
    local = local.split("+", 1)[0]
    if domain in {"gmail.com", "googlemail.com"}:
        local = local.replace(".", "")
    return f"{local}@{domain}"


# ── internals ─────────────────────────────────────────────────────


async def _lock_account(db: AsyncSession, email: str) -> AnalyzerCreditAccount:
    """Upsert-then-lock: materialize the row (so the lock is real for a
    brand-new email), then ``SELECT ... FOR UPDATE``. Caller is in a txn."""
    now = utc_now()
    await db.execute(
        pg_insert(AnalyzerCreditAccount)
        .values(email=email, created_at=now, updated_at=now)
        .on_conflict_do_nothing(index_elements=["email"])
    )
    return (
        await db.execute(
            select(AnalyzerCreditAccount)
            .where(AnalyzerCreditAccount.email == email)
            .with_for_update()
        )
    ).scalar_one()


async def _existing(
    db: AsyncSession, idempotency_key: str
) -> Optional[AnalyzerCreditLedger]:
    return (
        await db.execute(
            select(AnalyzerCreditLedger).where(
                AnalyzerCreditLedger.idempotency_key == idempotency_key
            )
        )
    ).scalar_one_or_none()


async def find_sale_grant(
    db: AsyncSession, *, sale_id: str
) -> Optional[AnalyzerCreditLedger]:
    """The existing GUMROAD_GRANT ledger row for a sale, if any — for the
    redeem '409 already redeemed' check."""
    return await _existing(db, f"gumroad-sale-{sale_id}")


async def find_reservation(
    db: AsyncSession, *, job_id: uuid.UUID
) -> Optional[AnalyzerCreditLedger]:
    """Return the existing reserve ledger row for a job, if completion retried."""
    return await _existing(db, f"reserve-{job_id}")


def _post(
    db: AsyncSession,
    account: AnalyzerCreditAccount,
    *,
    entry_type: AnalyzerCreditEntryType,
    direction: AnalyzerCreditDirection,
    amount: int,
    balance_before: int,
    idempotency_key: str,
    source: str,
    job_id: Optional[uuid.UUID] = None,
    gumroad_sale_id: Optional[str] = None,
    gumroad_license_key: Optional[str] = None,
    gumroad_permalink: Optional[str] = None,
    reversal_of_id: Optional[uuid.UUID] = None,
) -> AnalyzerCreditLedger:
    entry = AnalyzerCreditLedger(
        account_id=account.id,
        email=account.email,
        idempotency_key=idempotency_key,
        entry_type=entry_type,
        direction=direction,
        amount=amount,
        balance_before=balance_before,
        balance_after=account.remaining_credits,
        source=source,
        job_id=job_id,
        gumroad_sale_id=gumroad_sale_id,
        gumroad_license_key=gumroad_license_key,
        gumroad_permalink=gumroad_permalink,
        reversal_of_id=reversal_of_id,
    )
    db.add(entry)
    return entry


# ── submit: free-grant-or-paywall + reserve (one atomic unit) ─────


async def acquire_for_submit(
    db: AsyncSession, *, raw_email: str, job_id: uuid.UUID
) -> AnalyzerCreditLedger:
    """Secure one analysis credit for a guest submit, under the account lock:
    grant the free analysis if unused, else require a purchased credit; then
    RESERVE one. Raises ``NoCreditsError`` (→ 402) if none available. Does NOT
    commit — rides the submit's transaction (design §4.1/§6.2)."""
    email = canonicalize_email(raw_email)
    account = await _lock_account(db, email)

    # The free analysis is gated on free_used AND flagged_at: a refunded/
    # disputed account (flagged) loses free-tier re-access (design §7.7), even
    # if it never used the free one.
    if not account.free_used and account.flagged_at is None:
        key = f"free-{email}"
        if await _existing(db, key) is None:
            before = account.remaining_credits
            account.remaining_credits = before + 1
            account.free_used = True
            _post(
                db,
                account,
                entry_type=AnalyzerCreditEntryType.FREE_GRANT,
                direction=AnalyzerCreditDirection.CREDIT,
                amount=1,
                balance_before=before,
                idempotency_key=key,
                source="free",
            )

    if account.remaining_credits < 1:
        raise NoCreditsError(email)

    rkey = f"reserve-{job_id}"
    if (existing := await _existing(db, rkey)) is not None:
        return existing
    before = account.remaining_credits
    account.remaining_credits = before - 1
    account.reserved_credits += 1
    return _post(
        db,
        account,
        entry_type=AnalyzerCreditEntryType.RESERVE,
        direction=AnalyzerCreditDirection.DEBIT,
        amount=1,
        balance_before=before,
        idempotency_key=rkey,
        source="system",
        job_id=job_id,
    )


# ── worker: consume on success / refund on failure ───────────────


async def consume_reservation(
    db: AsyncSession, *, raw_email: str, job_id: uuid.UUID
) -> Optional[AnalyzerCreditLedger]:
    """Spend a reserved credit on a successful analysis (reserved→spent;
    ``remaining`` unchanged — it dropped at reserve). Idempotent. Does NOT
    commit — rides the worker's ``_write_completed`` txn (design §6.1)."""
    email = canonicalize_email(raw_email)
    key = f"consume-{job_id}"
    if (existing := await _existing(db, key)) is not None:
        return existing
    account = await _lock_account(db, email)
    if account.reserved_credits < 1:
        logger.warning("consume: no reservation for %s job=%s", email, job_id)
        return None
    account.reserved_credits -= 1
    account.lifetime_spent += 1
    return _post(
        db,
        account,
        entry_type=AnalyzerCreditEntryType.CONSUME,
        direction=AnalyzerCreditDirection.DEBIT,
        amount=1,
        balance_before=account.remaining_credits,
        idempotency_key=key,
        source="system",
        job_id=job_id,
    )


async def refund_reservation(
    db: AsyncSession, *, raw_email: str, job_id: uuid.UUID
) -> Optional[AnalyzerCreditLedger]:
    """Return a reserved credit on a failed analysis. Idempotent. Does NOT
    commit — rides the worker's ``_mark_failed`` txn (design §6.1)."""
    email = canonicalize_email(raw_email)
    key = f"refund-{job_id}"
    if (existing := await _existing(db, key)) is not None:
        return existing
    account = await _lock_account(db, email)
    if account.reserved_credits < 1:
        logger.warning("refund: no reservation for %s job=%s", email, job_id)
        return None
    reserve_entry = await _existing(db, f"reserve-{job_id}")
    before = account.remaining_credits
    account.remaining_credits = before + 1
    account.reserved_credits -= 1
    return _post(
        db,
        account,
        entry_type=AnalyzerCreditEntryType.REFUND,
        direction=AnalyzerCreditDirection.CREDIT,
        amount=1,
        balance_before=before,
        idempotency_key=key,
        source="system",
        job_id=job_id,
        reversal_of_id=reserve_entry.id if reserve_entry else None,
    )


# ── Gumroad: grant on sale / revoke on refund ────────────────────


async def grant_paid(
    db: AsyncSession,
    *,
    raw_email: str,
    permalink: str,
    sale_id: str,
    license_key: Optional[str] = None,
) -> Optional[AnalyzerCreditLedger]:
    """Grant paid credits for a VERIFIED Gumroad sale. Idempotent on the sale
    (``gumroad-sale-{sale_id}``); the webhook + the license-redeem converge on
    this key so a sale can't double-credit. Does NOT commit. Returns None for an
    unknown permalink."""
    amount = PERMALINK_CREDITS.get(permalink)
    if not amount:
        logger.warning("grant_paid: unknown permalink %s (sale %s)", permalink, sale_id)
        return None
    email = canonicalize_email(raw_email)
    key = f"gumroad-sale-{sale_id}"
    if (existing := await _existing(db, key)) is not None:
        return existing
    account = await _lock_account(db, email)
    before = account.remaining_credits
    account.remaining_credits = before + amount
    account.lifetime_purchased += amount
    return _post(
        db,
        account,
        entry_type=AnalyzerCreditEntryType.GUMROAD_GRANT,
        direction=AnalyzerCreditDirection.CREDIT,
        amount=amount,
        balance_before=before,
        idempotency_key=key,
        source="gumroad",
        gumroad_sale_id=sale_id,
        gumroad_license_key=license_key,
        gumroad_permalink=permalink,
    )


async def revoke_sale(
    db: AsyncSession, *, sale_id: str
) -> Optional[AnalyzerCreditLedger]:
    """Claw back credits on a Gumroad refund/dispute, against the account that
    ACTUALLY received the grant — resolved by ``sale_id``, because a redeemed
    sale may have credited a DIFFERENT email than the refund Ping carries
    (design §4.4). Clamps to the current balance (no negative debt — the report
    was already delivered, §7.5/§7.7) and flags THAT account out of the free
    tier. Idempotent on ``gumroad-revoke-{sale_id}``. If no grant exists for the
    sale, it is a NO-OP — we never flag an email that was never credited. Does
    NOT commit."""
    key = f"gumroad-revoke-{sale_id}"
    if (existing := await _existing(db, key)) is not None:
        return existing
    grant = await find_sale_grant(db, sale_id=sale_id)
    if grant is None:
        # Nothing was granted for this sale → nothing to claw back, and no
        # innocent email to flag.
        return None
    account = await _lock_account(db, grant.email)
    before = account.remaining_credits
    applied = min(grant.amount, before)  # clamp at 0; no negative balance
    account.remaining_credits = before - applied
    account.flagged_at = utc_now()
    # amount records the SALE's credit count (>0); balance snapshots show the
    # clamped actual change. Do NOT set gumroad_sale_id — the GRANT row holds it
    # under a unique constraint; the revoke dedups via idempotency_key and links
    # back to the grant via reversal_of_id.
    return _post(
        db,
        account,
        entry_type=AnalyzerCreditEntryType.REVOKE,
        direction=AnalyzerCreditDirection.DEBIT,
        amount=grant.amount,
        balance_before=before,
        idempotency_key=key,
        source="gumroad",
        gumroad_permalink=grant.gumroad_permalink,
        reversal_of_id=grant.id,
    )


# ── read: balance for GET /ai/public/credits ─────────────────────


async def get_balance(db: AsyncSession, *, raw_email: str) -> dict:
    """Coarse balance for an email (no lock). Returns remaining_credits +
    free_used + can_submit_free; the router decides what to expose without a
    token (free_used is the email-enumeration leak, §4.3)."""
    email = canonicalize_email(raw_email)
    account = (
        await db.execute(
            select(AnalyzerCreditAccount).where(AnalyzerCreditAccount.email == email)
        )
    ).scalar_one_or_none()
    if account is None:
        return {"remaining_credits": 0, "free_used": False, "can_submit_free": True}
    return {
        "remaining_credits": account.remaining_credits,
        "free_used": account.free_used,
        "can_submit_free": not account.free_used and account.flagged_at is None,
    }
