"""Recompute materialized account balances from journal lines.

Recompute-from-lines (DECIDED §11.3 of the impl plan): on each post we
re-derive the affected (account, period) balances from journal_lines rather
than incrementing in place — correctness over speed. Reversed entries and their
reversing entries both contribute lines, so they net naturally.

Sign convention: closing_minor = opening_minor + debits - credits (debit-positive).
A balanced ledger sums to zero across all accounts in a period. Cross-period
opening carry-forward is a period-close concern (later phase); opening stays 0
until then.
"""

from __future__ import annotations

import uuid
from typing import Iterable

from services.ledger_service.models import AccountBalance, JournalEntry, JournalLine
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession


async def recompute_account_balances(
    session: AsyncSession,
    org_id: uuid.UUID,
    period_id: uuid.UUID,
    account_ids: Iterable[uuid.UUID],
) -> None:
    """Recompute (org, account, period) balance rows for the given accounts."""
    for account_id in set(account_ids):
        debits, credits, currency = (
            await session.execute(
                select(
                    func.coalesce(func.sum(JournalLine.debit_minor), 0),
                    func.coalesce(func.sum(JournalLine.credit_minor), 0),
                    func.min(JournalLine.currency),
                )
                .select_from(JournalLine)
                .join(JournalEntry, JournalLine.entry_id == JournalEntry.id)
                .where(
                    JournalLine.org_id == org_id,
                    JournalLine.account_id == account_id,
                    JournalEntry.period_id == period_id,
                )
            )
        ).one()
        currency = currency or "NGN"

        bal = await session.get(AccountBalance, (org_id, account_id, period_id))
        opening = bal.opening_minor if bal is not None else 0
        closing = opening + debits - credits
        if bal is None:
            session.add(
                AccountBalance(
                    org_id=org_id,
                    account_id=account_id,
                    period_id=period_id,
                    opening_minor=0,
                    debits_minor=debits,
                    credits_minor=credits,
                    closing_minor=closing,
                    currency=currency,
                )
            )
        else:
            bal.debits_minor = debits
            bal.credits_minor = credits
            bal.closing_minor = closing
            bal.currency = currency
    await session.flush()
