"""Admin / finance read + report routes (role-gated, design doc §15).

Reads (accounts, journal entries, reports) require viewer+. Manual journal
entries and reversals (accountant+) live in routers/manual.py. Finance-user
management (admin+) lives in routers/users.py.

Gateway proxies /api/v1/admin/finance/{path} -> /admin/finance/{path}.
"""

from __future__ import annotations

import uuid
from datetime import date, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from libs.common.datetime_utils import utc_now
from services.ledger_service.app.deps import get_ledger_db, require_ledger_role
from services.ledger_service.models import ChartOfAccounts, JournalEntry, JournalLine
from services.ledger_service.models.enums import LedgerRole
from services.ledger_service.schemas.reports import (
    AccountOut,
    JournalEntryDetail,
    JournalEntrySummary,
    JournalLineOut,
    ProfitLossReport,
    TrialBalanceReport,
)
from services.ledger_service.services.reports import profit_loss, trial_balance
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/admin/finance", tags=["ledger-admin"])


@router.get("/accounts", response_model=list[AccountOut])
async def list_accounts(
    request: Request,
    _viewer=Depends(require_ledger_role(LedgerRole.VIEWER)),
    session: AsyncSession = Depends(get_ledger_db),
    active_only: bool = True,
) -> list[ChartOfAccounts]:
    org_id = request.state.org_id
    query = select(ChartOfAccounts).where(ChartOfAccounts.org_id == org_id)
    if active_only:
        query = query.where(ChartOfAccounts.is_active.is_(True))
    rows = (await session.execute(query.order_by(ChartOfAccounts.code))).scalars().all()
    return list(rows)


@router.get("/journal-entries", response_model=list[JournalEntrySummary])
async def list_journal_entries(
    request: Request,
    _viewer=Depends(require_ledger_role(LedgerRole.VIEWER)),
    session: AsyncSession = Depends(get_ledger_db),
    source_service: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
) -> list[JournalEntry]:
    org_id = request.state.org_id
    query = select(JournalEntry).where(JournalEntry.org_id == org_id)
    if source_service:
        query = query.where(JournalEntry.source_service == source_service)
    query = query.order_by(
        JournalEntry.entry_date.desc(), JournalEntry.posting_date.desc()
    ).limit(limit)
    rows = (await session.execute(query)).scalars().all()
    return list(rows)


@router.get("/journal-entries/{entry_id}", response_model=JournalEntryDetail)
async def get_journal_entry(
    entry_id: uuid.UUID,
    request: Request,
    _viewer=Depends(require_ledger_role(LedgerRole.VIEWER)),
    session: AsyncSession = Depends(get_ledger_db),
) -> JournalEntryDetail:
    org_id = request.state.org_id
    entry = await session.get(JournalEntry, entry_id)
    if entry is None or entry.org_id != org_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Journal entry not found"
        )
    line_rows = (
        await session.execute(
            select(JournalLine, ChartOfAccounts.code)
            .join(ChartOfAccounts, JournalLine.account_id == ChartOfAccounts.id)
            .where(JournalLine.entry_id == entry_id)
        )
    ).all()
    lines = [
        JournalLineOut(
            account_id=line.account_id,
            account_code=code,
            debit_minor=line.debit_minor,
            credit_minor=line.credit_minor,
            currency=line.currency,
            cost_center_id=line.cost_center_id,
            dimension_1=line.dimension_1,
            dimension_2=line.dimension_2,
            member_ref=line.member_ref,
            external_ref=line.external_ref,
            description=line.description,
        )
        for line, code in line_rows
    ]
    summary = JournalEntrySummary.model_validate(entry).model_dump()
    return JournalEntryDetail(**summary, lines=lines)


@router.get("/reports/trial-balance", response_model=TrialBalanceReport)
async def get_trial_balance(
    request: Request,
    _viewer=Depends(require_ledger_role(LedgerRole.VIEWER)),
    session: AsyncSession = Depends(get_ledger_db),
    as_of: Optional[date] = None,
) -> TrialBalanceReport:
    org_id = request.state.org_id
    return await trial_balance(
        session, org_id, as_of or utc_now().astimezone(timezone.utc).date()
    )


@router.get("/reports/profit-loss", response_model=ProfitLossReport)
async def get_profit_loss(
    request: Request,
    from_date: date,
    to_date: date,
    _viewer=Depends(require_ledger_role(LedgerRole.VIEWER)),
    session: AsyncSession = Depends(get_ledger_db),
    group_by: str = "none",
) -> ProfitLossReport:
    org_id = request.state.org_id
    try:
        return await profit_loss(session, org_id, from_date, to_date, group_by)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
