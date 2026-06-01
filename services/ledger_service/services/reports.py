"""Financial reports derived from journal lines.

Trial balance and P&L are computed directly from journal_lines (joined to
entries for dates and accounts for type), debit-positive convention. Reversed
entries and their reversing entries both contribute lines, so they net.
"""

from __future__ import annotations

import uuid
from datetime import date

from services.ledger_service.models import ChartOfAccounts, JournalEntry, JournalLine
from services.ledger_service.models.enums import AccountType
from services.ledger_service.schemas.reports import (
    ProfitLossReport,
    ProfitLossRow,
    TrialBalanceReport,
    TrialBalanceRow,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

REVENUE_TYPES = (AccountType.REVENUE, AccountType.CONTRA_REVENUE)
EXPENSE_TYPES = (AccountType.EXPENSE, AccountType.CONTRA_EXPENSE)
SUPPORTED_GROUP_BY = ("none", "dimension_1")


async def trial_balance(
    session: AsyncSession, org_id: uuid.UUID, as_of: date
) -> TrialBalanceReport:
    """Per-account debit/credit balances for all entries on or before as_of."""
    rows = (
        await session.execute(
            select(
                ChartOfAccounts.code,
                ChartOfAccounts.name,
                ChartOfAccounts.type,
                func.coalesce(func.sum(JournalLine.debit_minor), 0),
                func.coalesce(func.sum(JournalLine.credit_minor), 0),
            )
            .select_from(JournalLine)
            .join(JournalEntry, JournalLine.entry_id == JournalEntry.id)
            .join(ChartOfAccounts, JournalLine.account_id == ChartOfAccounts.id)
            .where(
                JournalLine.org_id == org_id,
                JournalEntry.entry_date <= as_of,
            )
            .group_by(ChartOfAccounts.code, ChartOfAccounts.name, ChartOfAccounts.type)
            .order_by(ChartOfAccounts.code)
        )
    ).all()

    out: list[TrialBalanceRow] = []
    total_debit = total_credit = 0
    for code, name, type_, debit, credit in rows:
        net = debit - credit
        dr = net if net > 0 else 0
        cr = -net if net < 0 else 0
        if dr == 0 and cr == 0:
            continue  # skip flat accounts
        total_debit += dr
        total_credit += cr
        out.append(
            TrialBalanceRow(
                code=code,
                name=name,
                type=type_.value,
                debit_minor=dr,
                credit_minor=cr,
            )
        )
    return TrialBalanceReport(
        as_of=as_of,
        rows=out,
        total_debit_minor=total_debit,
        total_credit_minor=total_credit,
        balanced=(total_debit == total_credit),
    )


async def profit_loss(
    session: AsyncSession,
    org_id: uuid.UUID,
    from_date: date,
    to_date: date,
    group_by: str = "none",
) -> ProfitLossReport:
    """Revenue/expense over [from_date, to_date], grouped by account or domain.

    group_by: "none" (per account) or "dimension_1" (per domain tag).
    Revenue = net credit on revenue-type accounts; expense = net debit on
    expense-type accounts; net_income = revenue - expense.
    """
    if group_by not in SUPPORTED_GROUP_BY:
        raise ValueError(f"unsupported group_by: {group_by}")

    if group_by == "dimension_1":
        key_col = func.coalesce(JournalLine.dimension_1, "(unassigned)")
    else:
        key_col = ChartOfAccounts.code

    rows = (
        await session.execute(
            select(
                key_col.label("key"),
                ChartOfAccounts.name,
                ChartOfAccounts.type,
                func.coalesce(func.sum(JournalLine.credit_minor), 0),
                func.coalesce(func.sum(JournalLine.debit_minor), 0),
            )
            .select_from(JournalLine)
            .join(JournalEntry, JournalLine.entry_id == JournalEntry.id)
            .join(ChartOfAccounts, JournalLine.account_id == ChartOfAccounts.id)
            .where(
                JournalLine.org_id == org_id,
                JournalEntry.entry_date >= from_date,
                JournalEntry.entry_date <= to_date,
                ChartOfAccounts.type.in_(REVENUE_TYPES + EXPENSE_TYPES),
            )
            .group_by(key_col, ChartOfAccounts.name, ChartOfAccounts.type)
            .order_by(key_col)
        )
    ).all()

    # Fold (key, type) rows into per-key revenue/expense.
    agg: dict[str, dict] = {}
    for key, name, type_, credit, debit in rows:
        label = f"{key} — {name}" if group_by == "none" else str(key)
        bucket = agg.setdefault(label, {"revenue": 0, "expense": 0})
        if type_ in REVENUE_TYPES:
            bucket["revenue"] += credit - debit
        else:
            bucket["expense"] += debit - credit

    out: list[ProfitLossRow] = []
    total_revenue = total_expense = 0
    for label, b in agg.items():
        rev, exp = b["revenue"], b["expense"]
        total_revenue += rev
        total_expense += exp
        out.append(
            ProfitLossRow(
                key=label,
                revenue_minor=rev,
                expense_minor=exp,
                net_minor=rev - exp,
            )
        )
    return ProfitLossReport(
        from_date=from_date,
        to_date=to_date,
        group_by=group_by,
        rows=out,
        total_revenue_minor=total_revenue,
        total_expense_minor=total_expense,
        net_income_minor=total_revenue - total_expense,
    )
