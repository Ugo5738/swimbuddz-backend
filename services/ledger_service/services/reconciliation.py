"""Settlement reconciliation engine (design §11.2).

Takes PSP settlement transactions pushed by payments_service, stores them as
``ExternalTransaction`` rows, and matches each against the books by
``journal_lines.external_ref`` (= our payment reference). Outcomes:

  - **matched**        — a journal line with that ref exists and the gross ties out.
  - **amount_mismatch** — the ref is in the books but the amount differs → break.
  - **unmatched**      — no journal line for that ref (money settled, never booked
                          — e.g. a wallet top-up that never posted) → break.

Everything is idempotent: transactions upsert by (org, psp, external_txn_id);
breaks upsert by (org, break_type, external_ref); a later matching pass resolves
a break once the missing entry appears.
"""

from __future__ import annotations

from typing import Optional

from libs.common.datetime_utils import utc_now
from services.ledger_service.models import (
    ExternalTransaction,
    JournalLine,
    ReconciliationBreak,
)
from services.ledger_service.schemas.reconciliation import (
    ReconciliationBreakOut,
    ReconciliationReport,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession


async def _open_break(
    session: AsyncSession,
    org_id,
    break_type: str,
    txn: ExternalTransaction,
    *,
    expected: Optional[int],
    detail: str,
) -> bool:
    """Upsert an open break for this (org, type, ref). Returns True if newly opened."""
    existing = (
        await session.execute(
            select(ReconciliationBreak).where(
                ReconciliationBreak.org_id == org_id,
                ReconciliationBreak.break_type == break_type,
                ReconciliationBreak.external_ref == txn.external_ref,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        # Refresh the live numbers; reopen if it had been resolved but recurs.
        existing.actual_minor = txn.amount_minor
        existing.expected_minor = expected
        existing.external_txn_id = txn.external_txn_id
        existing.settlement_ref = txn.settlement_ref
        existing.detail = detail
        if existing.status != "open":
            existing.status = "open"
            existing.resolved_at = None
            existing.resolved_by = None
            return True
        return False
    session.add(
        ReconciliationBreak(
            org_id=org_id,
            break_type=break_type,
            psp=txn.psp,
            external_ref=txn.external_ref,
            external_txn_id=txn.external_txn_id,
            settlement_ref=txn.settlement_ref,
            expected_minor=expected,
            actual_minor=txn.amount_minor,
            currency=txn.currency or "NGN",
            status="open",
            detail=detail,
        )
    )
    return True


async def _resolve_breaks(session: AsyncSession, org_id, external_ref: str) -> None:
    """Resolve any open breaks for this ref (the entry has now appeared/ties out)."""
    if not external_ref:
        return
    open_breaks = (
        (
            await session.execute(
                select(ReconciliationBreak).where(
                    ReconciliationBreak.org_id == org_id,
                    ReconciliationBreak.external_ref == external_ref,
                    ReconciliationBreak.status == "open",
                )
            )
        )
        .scalars()
        .all()
    )
    for b in open_breaks:
        b.status = "resolved"
        b.resolved_at = utc_now()
        b.resolved_by = "auto-match"


async def _match_one(
    session: AsyncSession, org_id, txn: ExternalTransaction
) -> tuple[str, bool]:
    """Match one external txn against the books. Returns (match_status, break_opened)."""
    if not txn.external_ref:
        txn.match_status = "unmatched"
        opened = await _open_break(
            session,
            org_id,
            "unmatched_settlement",
            txn,
            expected=None,
            detail="Settlement transaction carries no reference to match.",
        )
        return "unmatched", opened

    lines = (
        await session.execute(
            select(
                JournalLine.entry_id,
                JournalLine.debit_minor,
                JournalLine.credit_minor,
            ).where(
                JournalLine.org_id == org_id,
                JournalLine.external_ref == txn.external_ref,
            )
        )
    ).all()

    if not lines:
        txn.match_status = "unmatched"
        opened = await _open_break(
            session,
            org_id,
            "unmatched_settlement",
            txn,
            expected=None,
            detail=(
                "Settled at the PSP but no journal entry carries this reference "
                "(money in, not booked)."
            ),
        )
        return "unmatched", opened

    # gross-in-books = the largest debit line for this ref (the clearing/bank side
    # of the cash-in entry). Prefer the entry whose debit equals the txn amount.
    gross = max((line.debit_minor for line in lines), default=0)
    matched_entry = next(
        (line.entry_id for line in lines if line.debit_minor == txn.amount_minor),
        lines[0].entry_id,
    )
    txn.matched_entry_id = matched_entry

    amount_ok = any(
        line.debit_minor == txn.amount_minor or line.credit_minor == txn.amount_minor
        for line in lines
    )
    if amount_ok:
        txn.match_status = "matched"
        await _resolve_breaks(session, org_id, txn.external_ref)
        return "matched", False

    txn.match_status = "amount_mismatch"
    opened = await _open_break(
        session,
        org_id,
        "amount_mismatch",
        txn,
        expected=gross,
        detail=f"PSP amount {txn.amount_minor} != booked gross {gross}.",
    )
    return "amount_mismatch", opened


async def intake_external_transactions(
    session: AsyncSession, org_id, transactions: list
) -> dict:
    """Upsert pushed PSP transactions and match each against the books.

    ``transactions`` is a list of ExternalTransactionIn (Pydantic). Idempotent
    per (org, psp, external_txn_id). The caller commits.
    """
    summary = {
        "received": len(transactions),
        "inserted": 0,
        "matched": 0,
        "breaks_opened": 0,
    }
    for t in transactions:
        row = (
            await session.execute(
                select(ExternalTransaction).where(
                    ExternalTransaction.org_id == org_id,
                    ExternalTransaction.psp == t.psp,
                    ExternalTransaction.external_txn_id == t.external_txn_id,
                )
            )
        ).scalar_one_or_none()

        if row is None:
            row = ExternalTransaction(
                org_id=org_id,
                psp=t.psp,
                external_txn_id=t.external_txn_id,
                external_ref=t.external_ref,
                settlement_ref=t.settlement_ref,
                amount_minor=int(t.amount_minor or 0),
                fee_minor=int(t.fee_minor or 0),
                currency=t.currency or "NGN",
                status=t.status,
                occurred_at=t.occurred_at,
                raw_payload=t.raw_payload,
            )
            session.add(row)
            await session.flush()
            summary["inserted"] += 1
        else:
            # Settlement may re-report the same txn — refresh mutable fields.
            row.settlement_ref = t.settlement_ref or row.settlement_ref
            row.amount_minor = int(t.amount_minor or row.amount_minor)
            row.fee_minor = int(t.fee_minor or row.fee_minor)
            row.status = t.status or row.status

        status, break_opened = await _match_one(session, org_id, row)
        if status == "matched":
            summary["matched"] += 1
        if break_opened:
            summary["breaks_opened"] += 1

    return summary


async def reconciliation_report(
    session: AsyncSession, org_id, limit: int = 200
) -> ReconciliationReport:
    """Open breaks + a small summary for the admin reconciliation view."""
    breaks = (
        (
            await session.execute(
                select(ReconciliationBreak)
                .where(
                    ReconciliationBreak.org_id == org_id,
                    ReconciliationBreak.status == "open",
                )
                .order_by(ReconciliationBreak.created_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    open_count = (
        await session.execute(
            select(func.count())
            .select_from(ReconciliationBreak)
            .where(
                ReconciliationBreak.org_id == org_id,
                ReconciliationBreak.status == "open",
            )
        )
    ).scalar() or 0
    open_amount = (
        await session.execute(
            select(func.coalesce(func.sum(ReconciliationBreak.actual_minor), 0)).where(
                ReconciliationBreak.org_id == org_id,
                ReconciliationBreak.status == "open",
            )
        )
    ).scalar() or 0
    matched_count = (
        await session.execute(
            select(func.count())
            .select_from(ExternalTransaction)
            .where(
                ExternalTransaction.org_id == org_id,
                ExternalTransaction.match_status == "matched",
            )
        )
    ).scalar() or 0
    unmatched_count = (
        await session.execute(
            select(func.count())
            .select_from(ExternalTransaction)
            .where(
                ExternalTransaction.org_id == org_id,
                ExternalTransaction.match_status != "matched",
            )
        )
    ).scalar() or 0

    return ReconciliationReport(
        open_breaks=int(open_count),
        open_break_amount_minor=int(open_amount),
        matched_count=int(matched_count),
        unmatched_count=int(unmatched_count),
        breaks=[ReconciliationBreakOut.model_validate(b) for b in breaks],
    )
