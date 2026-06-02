"""Financial reports derived from journal lines.

Trial balance and P&L are computed directly from journal_lines (joined to
entries for dates and accounts for type), debit-positive convention. Reversed
entries and their reversing entries both contribute lines, so they net.
"""

from __future__ import annotations

import uuid
from datetime import date

from services.ledger_service.models import (
    ChartOfAccounts,
    JournalEntry,
    JournalLine,
    RevenueRecognitionSchedule,
)
from services.ledger_service.models.enums import AccountType
from services.ledger_service.schemas.reports import (
    BalanceSheetReport,
    BalanceSheetRow,
    BalanceSheetSection,
    BubblesLiabilityReport,
    CashPositionReport,
    CashPositionRow,
    DeferredRevenueReport,
    DeferredRevenueRow,
    MarginReport,
    MarginRow,
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

# Cash & in-transit accounts for the cash-position report, keyed by stable
# maps_to ref → "bank" (settled) vs "clearing" (collected, not yet settled).
CASH_ACCOUNTS = {
    "bank_operating_ngn": "bank",
    "bank_operating_usd": "bank",
    "paystack_clearing": "clearing",
    "flutterwave_clearing": "clearing",
}


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


async def deferred_revenue(
    session: AsyncSession, org_id: uuid.UUID, as_of: date
) -> DeferredRevenueReport:
    """Outstanding deferred-revenue obligations — what's still owed in service.

    Aggregates recognition schedules (start_date <= as_of) by deferred account +
    domain: total booked, recognised to date, and remaining (total - recognised).
    The remaining total ties to the deferred-revenue account balances.
    """
    domain_col = func.coalesce(RevenueRecognitionSchedule.dimension_1, "(unassigned)")
    rows = (
        await session.execute(
            select(
                RevenueRecognitionSchedule.deferred_account_ref,
                domain_col,
                func.count(),
                func.coalesce(func.sum(RevenueRecognitionSchedule.total_minor), 0),
                func.coalesce(func.sum(RevenueRecognitionSchedule.recognized_minor), 0),
            )
            .where(
                RevenueRecognitionSchedule.org_id == org_id,
                RevenueRecognitionSchedule.start_date <= as_of,
            )
            .group_by(RevenueRecognitionSchedule.deferred_account_ref, domain_col)
            .order_by(RevenueRecognitionSchedule.deferred_account_ref)
        )
    ).all()
    out: list[DeferredRevenueRow] = []
    total_remaining = 0
    for acct, domain, count, total, recognized in rows:
        remaining = total - recognized
        total_remaining += remaining
        out.append(
            DeferredRevenueRow(
                deferred_account_ref=acct,
                domain=domain,
                schedule_count=count,
                total_minor=total,
                recognized_minor=recognized,
                remaining_minor=remaining,
            )
        )
    return DeferredRevenueReport(
        as_of=as_of, rows=out, total_remaining_minor=total_remaining
    )


async def balance_sheet(
    session: AsyncSession, org_id: uuid.UUID, as_of: date
) -> BalanceSheetReport:
    """Statement of financial position as of a date: A = L + E.

    P&L accounts aren't closed to retained earnings yet (no period-close entry),
    so net income to date is surfaced as a synthetic "Current-Year Earnings"
    equity row. Because every journal entry balances, total assets always equals
    total liabilities + equity (incl. that row) — ``balanced`` is the guard.
    """
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
            .where(JournalLine.org_id == org_id, JournalEntry.entry_date <= as_of)
            .group_by(ChartOfAccounts.code, ChartOfAccounts.name, ChartOfAccounts.type)
            .order_by(ChartOfAccounts.code)
        )
    ).all()

    assets: list[BalanceSheetRow] = []
    liabilities: list[BalanceSheetRow] = []
    equity: list[BalanceSheetRow] = []
    total_assets = total_liab = total_equity = net_income = 0

    for code, name, type_, debit, credit in rows:
        net_debit = debit - credit
        net_credit = credit - debit
        if type_ in (AccountType.ASSET, AccountType.CONTRA_ASSET):
            if net_debit:
                assets.append(
                    BalanceSheetRow(code=code, name=name, amount_minor=net_debit)
                )
                total_assets += net_debit
        elif type_ == AccountType.LIABILITY:
            if net_credit:
                liabilities.append(
                    BalanceSheetRow(code=code, name=name, amount_minor=net_credit)
                )
                total_liab += net_credit
        elif type_ == AccountType.EQUITY:
            if net_credit:
                equity.append(
                    BalanceSheetRow(code=code, name=name, amount_minor=net_credit)
                )
                total_equity += net_credit
        elif type_ in REVENUE_TYPES:
            net_income += net_credit
        elif type_ in EXPENSE_TYPES:
            net_income -= net_debit

    if net_income:
        equity.append(
            BalanceSheetRow(
                code="3990",
                name="Current-Year Earnings (unclosed)",
                amount_minor=net_income,
            )
        )
        total_equity += net_income

    total_le = total_liab + total_equity
    return BalanceSheetReport(
        as_of=as_of,
        assets=BalanceSheetSection(rows=assets, total_minor=total_assets),
        liabilities=BalanceSheetSection(rows=liabilities, total_minor=total_liab),
        equity=BalanceSheetSection(rows=equity, total_minor=total_equity),
        total_assets_minor=total_assets,
        total_liabilities_and_equity_minor=total_le,
        balanced=(total_assets == total_le),
    )


async def margin_by_domain(
    session: AsyncSession, org_id: uuid.UUID, from_date: date, to_date: date
) -> MarginReport:
    """Gross margin per domain over [from_date, to_date]: revenue − COGS.

    COGS = cost-of-sales accounts (code 5xxx); operating expenses (6xxx) are
    excluded — this is gross, not net, margin. Grouped by ``dimension_1`` (the
    domain tag emitters stamp on revenue + COGS lines).
    """
    domain_col = func.coalesce(JournalLine.dimension_1, "(unassigned)")
    rows = (
        await session.execute(
            select(
                domain_col.label("domain"),
                ChartOfAccounts.type,
                ChartOfAccounts.code,
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
            .group_by(domain_col, ChartOfAccounts.type, ChartOfAccounts.code)
        )
    ).all()

    agg: dict[str, dict] = {}
    for domain, type_, code, credit, debit in rows:
        bucket = agg.setdefault(domain, {"revenue": 0, "cogs": 0})
        if type_ in REVENUE_TYPES:
            bucket["revenue"] += credit - debit
        elif type_ in EXPENSE_TYPES and str(code).startswith("5"):
            bucket["cogs"] += debit - credit

    out: list[MarginRow] = []
    total_rev = total_cogs = 0
    for domain in sorted(agg):
        rev = agg[domain]["revenue"]
        cogs = agg[domain]["cogs"]
        if rev == 0 and cogs == 0:
            continue
        total_rev += rev
        total_cogs += cogs
        out.append(
            MarginRow(
                domain=domain,
                revenue_minor=rev,
                cogs_minor=cogs,
                margin_minor=rev - cogs,
                margin_pct=round((rev - cogs) / rev * 100, 1) if rev else 0.0,
            )
        )
    return MarginReport(
        from_date=from_date,
        to_date=to_date,
        rows=out,
        total_revenue_minor=total_rev,
        total_cogs_minor=total_cogs,
        total_margin_minor=total_rev - total_cogs,
    )


async def bubbles_liability(
    session: AsyncSession, org_id: uuid.UUID, as_of: date
) -> BubblesLiabilityReport:
    """Outstanding Bubbles liability split into purchased vs promotional (§19-B)."""
    maps_to = ChartOfAccounts.account_metadata["maps_to"].astext
    rows = (
        await session.execute(
            select(
                maps_to,
                func.coalesce(func.sum(JournalLine.credit_minor), 0)
                - func.coalesce(func.sum(JournalLine.debit_minor), 0),
            )
            .select_from(JournalLine)
            .join(JournalEntry, JournalLine.entry_id == JournalEntry.id)
            .join(ChartOfAccounts, JournalLine.account_id == ChartOfAccounts.id)
            .where(
                JournalLine.org_id == org_id,
                JournalEntry.entry_date <= as_of,
                maps_to.in_(["bubbles_liability", "bubbles_liability_promo"]),
            )
            .group_by(maps_to)
        )
    ).all()
    balances = {m: int(bal) for m, bal in rows}
    purchased = balances.get("bubbles_liability", 0)
    promo = balances.get("bubbles_liability_promo", 0)
    return BubblesLiabilityReport(
        as_of=as_of,
        purchased_minor=purchased,
        promotional_minor=promo,
        total_minor=purchased + promo,
    )


async def cash_position(
    session: AsyncSession, org_id: uuid.UUID, as_of: date
) -> CashPositionReport:
    """Cash by location: settled in bank vs collected-but-in-transit at the PSP.

    Clearing nets toward zero once settlements are ingested (R3); a large
    clearing balance means cash collected that the bank hasn't settled yet.
    """
    maps_to = ChartOfAccounts.account_metadata["maps_to"].astext
    rows = (
        await session.execute(
            select(
                ChartOfAccounts.code,
                ChartOfAccounts.name,
                maps_to,
                func.coalesce(func.sum(JournalLine.debit_minor), 0)
                - func.coalesce(func.sum(JournalLine.credit_minor), 0),
            )
            .select_from(JournalLine)
            .join(JournalEntry, JournalLine.entry_id == JournalEntry.id)
            .join(ChartOfAccounts, JournalLine.account_id == ChartOfAccounts.id)
            .where(
                JournalLine.org_id == org_id,
                JournalEntry.entry_date <= as_of,
                maps_to.in_(list(CASH_ACCOUNTS)),
            )
            .group_by(ChartOfAccounts.code, ChartOfAccounts.name, maps_to)
            .order_by(ChartOfAccounts.code)
        )
    ).all()

    out: list[CashPositionRow] = []
    bank = clearing = 0
    for code, name, m, net_debit in rows:
        kind = CASH_ACCOUNTS.get(m, "bank")
        out.append(
            CashPositionRow(
                code=code, name=name, kind=kind, amount_minor=int(net_debit)
            )
        )
        if kind == "bank":
            bank += int(net_debit)
        else:
            clearing += int(net_debit)
    return CashPositionReport(
        as_of=as_of,
        rows=out,
        bank_minor=bank,
        clearing_minor=clearing,
        total_minor=bank + clearing,
    )
