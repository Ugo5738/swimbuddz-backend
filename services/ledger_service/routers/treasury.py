# fmt: off
"""Controlled treasury evidence and approval workflow.

No endpoint triggers an external bank payment. A request can be approved only by
a different finance OWNER, and confirmed only with real imported bank evidence.
All queries are scoped by org_id; the bank remains the final payment authority.
"""
import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from libs.common.datetime_utils import utc_now
from services.ledger_service.app.deps import get_ledger_db, require_ledger_role
from services.ledger_service.models import (
    ChartOfAccounts, JournalEntry, JournalLine,
    TreasuryAuditEvent, TreasuryBankAccount, TreasuryBudget,
    TreasuryRequest, TreasuryStatementLine,
)
from services.ledger_service.models.enums import AccountType, LedgerRole, NormalBalance
from services.ledger_service.schemas.treasury import (
    BankCreate, BankOut, BudgetCreate, BudgetOut, ConfirmRequest, Decision,
    MatchJournal, LedgerAccountLink, RequestCreate, RequestOut, StatementImport,
    StatementOut, TreasurySummary,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/admin/finance/treasury", tags=["treasury"])


def _audit(session, org, actor, action, subject, **details):
    session.add(TreasuryAuditEvent(
        org_id=org, actor_id=actor.id, action=action,
        subject_id=str(subject), details=details,
    ))


async def _bank(session, org, bank_id):
    bank = await session.scalar(select(TreasuryBankAccount).where(
        TreasuryBankAccount.org_id == org, TreasuryBankAccount.id == bank_id,
    ))
    if bank is None:
        raise HTTPException(404, "Bank account not found")
    return bank


async def _request(session, org, request_id, *, lock=False):
    stmt = select(TreasuryRequest).where(
        TreasuryRequest.org_id == org, TreasuryRequest.id == request_id,
    )
    if lock:
        stmt = stmt.with_for_update()
    item = await session.scalar(stmt)
    if item is None:
        raise HTTPException(404, "Request not found")
    return item


@router.get("/banks", response_model=list[BankOut])
async def list_banks(request: Request, _actor=Depends(require_ledger_role(LedgerRole.VIEWER)), session: AsyncSession = Depends(get_ledger_db)):
    return list((await session.scalars(select(TreasuryBankAccount).where(
        TreasuryBankAccount.org_id == request.state.org_id,
    ).order_by(TreasuryBankAccount.code))).all())


@router.post("/banks", response_model=BankOut, status_code=201)
async def create_bank(payload: BankCreate, request: Request, actor=Depends(require_ledger_role(LedgerRole.OWNER)), session: AsyncSession = Depends(get_ledger_db)):
    org = request.state.org_id
    existing = await session.scalar(select(TreasuryBankAccount).where(TreasuryBankAccount.org_id == org, TreasuryBankAccount.code == payload.code))
    if existing:
        raise HTTPException(409, "This bank already exists. No duplicate account was created.")
    bank = TreasuryBankAccount(
        org_id=org, code=payload.code,
        bank_name={"stanbic": "Stanbic IBTC", "moniepoint": "Moniepoint"}[payload.code],
        purpose={"stanbic": "treasury", "moniepoint": "operations"}[payload.code],
        last_four=payload.last_four,
    )
    session.add(bank)
    await session.flush()
    _audit(session, org, actor, "bank_created", bank.id, code=bank.code)
    await session.commit()
    await session.refresh(bank)
    return bank


@router.get("/ledger-options")
async def ledger_options(request: Request, _actor=Depends(require_ledger_role(LedgerRole.VIEWER)), session: AsyncSession = Depends(get_ledger_db)):
    org = request.state.org_id
    assets = (await session.scalars(select(ChartOfAccounts).where(
        ChartOfAccounts.org_id == org,
        ChartOfAccounts.is_active.is_(True),
        ChartOfAccounts.type == AccountType.ASSET,
        ChartOfAccounts.normal_balance == NormalBalance.DEBIT,
    ).order_by(ChartOfAccounts.code))).all()
    return [{"id": str(a.id), "code": a.code, "name": a.name} for a in assets
            if a.code.startswith("11") and a.code != "1100"]


@router.post("/banks/{bank_id}/link-ledger", response_model=BankOut)
async def link_bank_ledger(bank_id: uuid.UUID, payload: LedgerAccountLink, request: Request, actor=Depends(require_ledger_role(LedgerRole.OWNER)), session: AsyncSession = Depends(get_ledger_db)):
    org = request.state.org_id
    bank = await _bank(session, org, bank_id)
    account = await session.scalar(select(ChartOfAccounts).where(
        ChartOfAccounts.id == payload.account_id,
        ChartOfAccounts.org_id == org, ChartOfAccounts.is_active.is_(True),
    ))
    if account is None or account.type != AccountType.ASSET or account.normal_balance != NormalBalance.DEBIT:
        raise HTTPException(422, "Only an active bank asset ledger account can be linked")
    expected = "bank_operating_ngn" if bank.code == "stanbic" else "bank_moniepoint_ngn"
    if not account.account_metadata or account.account_metadata.get("maps_to") != expected:
        raise HTTPException(422, "Select the designated ledger account for this bank; do not mix bank balances")
    other = await session.scalar(select(TreasuryBankAccount).where(
        TreasuryBankAccount.org_id == org,
        TreasuryBankAccount.id != bank_id,
        TreasuryBankAccount.ledger_account_id == account.id,
    ))
    if other:
        raise HTTPException(409, "This ledger account is already linked to a different bank")
    bank.ledger_account_id = account.id
    _audit(session, org, actor, "bank_ledger_linked", bank.id, account_code=account.code)
    await session.commit()
    await session.refresh(bank)
    return bank


@router.post("/banks/{bank_id}/create-operations-ledger", response_model=BankOut)
async def create_operations_ledger(bank_id: uuid.UUID, request: Request, actor=Depends(require_ledger_role(LedgerRole.OWNER)), session: AsyncSession = Depends(get_ledger_db)):
    org = request.state.org_id
    bank = await _bank(session, org, bank_id)
    if bank.code != "moniepoint":
        raise HTTPException(422, "A dedicated account can only be created for Moniepoint operations")
    base = await session.scalar(select(ChartOfAccounts).where(
        ChartOfAccounts.org_id == org, ChartOfAccounts.code == "1110",
    ))
    if base is None or base.type != AccountType.ASSET:
        raise HTTPException(409, "The existing NGN bank account must be configured first")
    existing = await session.scalar(select(ChartOfAccounts).where(
        ChartOfAccounts.org_id == org, ChartOfAccounts.code == "1115",
    ))
    if existing:
        if existing.account_metadata != {"maps_to": "bank_moniepoint_ngn"}:
            raise HTTPException(409, "Account code 1115 already exists for a different purpose")
        account = existing
    else:
        account = ChartOfAccounts(
            org_id=org, code="1115", name="Bank — Moniepoint Operations (NGN)",
            type=AccountType.ASSET, normal_balance=NormalBalance.DEBIT,
            parent_id=base.parent_id, is_active=True, is_system=True,
            account_metadata={"maps_to": "bank_moniepoint_ngn"},
        )
        session.add(account)
        await session.flush()
    other = await session.scalar(select(TreasuryBankAccount).where(
        TreasuryBankAccount.org_id == org,
        TreasuryBankAccount.id != bank_id, TreasuryBankAccount.ledger_account_id == account.id,
    ))
    if other:
        raise HTTPException(409, "This ledger account belongs to a different bank")
    bank.ledger_account_id = account.id
    _audit(session, org, actor, "bank_operations_ledger_created", bank.id, account_code="1115")
    await session.commit()
    await session.refresh(bank)
    return bank


@router.get("/budgets", response_model=list[BudgetOut])
async def list_budgets(request: Request, _actor=Depends(require_ledger_role(LedgerRole.VIEWER)), session: AsyncSession = Depends(get_ledger_db)):
    return list((await session.scalars(select(TreasuryBudget).where(
        TreasuryBudget.org_id == request.state.org_id,
    ).order_by(TreasuryBudget.starts_on.desc()).limit(100))).all())


@router.post("/budgets", response_model=BudgetOut, status_code=201)
async def create_budget(payload: BudgetCreate, request: Request, actor=Depends(require_ledger_role(LedgerRole.OWNER)), session: AsyncSession = Depends(get_ledger_db)):
    org = request.state.org_id
    budget = TreasuryBudget(org_id=org, created_by=actor.id, **payload.model_dump())
    session.add(budget)
    await session.flush()
    _audit(session, org, actor, "budget_created", budget.id, amount_minor=budget.amount_minor)
    await session.commit()
    await session.refresh(budget)
    return budget


@router.get("/requests", response_model=list[RequestOut])
async def list_requests(request: Request, _actor=Depends(require_ledger_role(LedgerRole.VIEWER)), session: AsyncSession = Depends(get_ledger_db)):
    return list((await session.scalars(select(TreasuryRequest).where(
        TreasuryRequest.org_id == request.state.org_id,
    ).order_by(TreasuryRequest.created_at.desc()).limit(200))).all())


@router.post("/requests", response_model=RequestOut, status_code=201)
async def create_request(payload: RequestCreate, request: Request, actor=Depends(require_ledger_role(LedgerRole.OPERATIONS)), session: AsyncSession = Depends(get_ledger_db)):
    org = request.state.org_id
    source = await _bank(session, org, payload.source_bank_id)
    if payload.kind == "funding":
        dest = await _bank(session, org, payload.destination_bank_id)
        if source.purpose != "treasury" or dest.purpose != "operations":
            raise HTTPException(422, "Funding requests must move from treasury to operations")
    if payload.budget_id is not None:
        budget = await session.scalar(select(TreasuryBudget).where(TreasuryBudget.org_id == org, TreasuryBudget.id == payload.budget_id))
        if budget is None or budget.category != payload.category:
            raise HTTPException(422, "Budget not found or category mismatch")
        if not (budget.starts_on <= date.today() <= budget.ends_on):
            raise HTTPException(422, "Budget is not active")
    item = TreasuryRequest(org_id=org, requested_by=actor.id, **payload.model_dump())
    session.add(item)
    await session.flush()
    _audit(session, org, actor, "request_created", item.id, kind=item.kind, amount_minor=item.amount_minor)
    await session.commit()
    await session.refresh(item)
    return item


@router.post("/requests/{request_id}/decision", response_model=RequestOut)
async def decide_request(request_id: uuid.UUID, payload: Decision, request: Request, actor=Depends(require_ledger_role(LedgerRole.OWNER)), session: AsyncSession = Depends(get_ledger_db)):
    org = request.state.org_id
    item = await _request(session, org, request_id, lock=True)
    if item.status != "requested":
        raise HTTPException(409, "Only pending requests can be decided")
    if actor.id == item.requested_by:
        raise HTTPException(403, "You cannot approve your own request")
    if payload.action == "reject" and not (payload.reason or "").strip():
        raise HTTPException(422, "Rejection requires a reason")
    if payload.action == "approve" and item.budget_id is not None:
        budget = await session.scalar(select(TreasuryBudget).where(TreasuryBudget.org_id == org, TreasuryBudget.id == item.budget_id).with_for_update())
        if budget is None:
            raise HTTPException(409, "Budget was removed")
        allocated = await session.scalar(select(func.coalesce(func.sum(TreasuryRequest.amount_minor), 0)).where(
            TreasuryRequest.org_id == org, TreasuryRequest.budget_id == budget.id,
            TreasuryRequest.status.in_(("approved", "confirmed")),
        ))
        if int(allocated) + item.amount_minor > budget.amount_minor:
            raise HTTPException(409, "Budget exceeded: increase the budget through a separate authorisation")
    item.status = "approved" if payload.action == "approve" else "rejected"
    item.approved_by = actor.id if item.status == "approved" else None
    item.approved_at = utc_now() if item.status == "approved" else None
    item.rejected_reason = (payload.reason or "").strip() if item.status == "rejected" else None
    _audit(session, org, actor, "request_" + item.status, item.id, reason=item.rejected_reason)
    await session.commit()
    await session.refresh(item)
    return item


@router.get("/statements", response_model=list[StatementOut])
async def list_statements(request: Request, bank_id: uuid.UUID | None = None, _actor=Depends(require_ledger_role(LedgerRole.VIEWER)), session: AsyncSession = Depends(get_ledger_db)):
    stmt = select(TreasuryStatementLine).where(TreasuryStatementLine.org_id == request.state.org_id)
    if bank_id:
        await _bank(session, request.state.org_id, bank_id)
        stmt = stmt.where(TreasuryStatementLine.bank_account_id == bank_id)
    return list((await session.scalars(stmt.order_by(TreasuryStatementLine.occurred_on.desc(), TreasuryStatementLine.row_number.desc()).limit(250))).all())


@router.post("/banks/{bank_id}/statements")
async def import_statement(bank_id: uuid.UUID, payload: StatementImport, request: Request, actor=Depends(require_ledger_role(LedgerRole.OPERATIONS)), session: AsyncSession = Depends(get_ledger_db)):
    org = request.state.org_id
    await _bank(session, org, bank_id)
    if len(set(row.row_number for row in payload.rows)) != len(payload.rows):
        raise HTTPException(422, "A statement has repeated row numbers")
    # Lock the bank to serialize concurrent imports for this bank.
    await session.scalar(select(TreasuryBankAccount.id).where(TreasuryBankAccount.org_id == org, TreasuryBankAccount.id == bank_id).with_for_update())
    existing = list((await session.scalars(select(TreasuryStatementLine).where(
        TreasuryStatementLine.org_id == org,
        TreasuryStatementLine.bank_account_id == bank_id,
        TreasuryStatementLine.statement_id == payload.statement_id,
    ))).all())
    saved = {row.row_number: row for row in existing}
    # Guard a common operator mistake: importing the same referenced transaction
    # again under a different statement ID. Do this with one query, not N polls.
    incoming_refs = {row.reference for row in payload.rows if row.reference}
    other_rows = []
    if incoming_refs:
        other_rows = list((await session.scalars(select(TreasuryStatementLine).where(
            TreasuryStatementLine.org_id == org,
            TreasuryStatementLine.bank_account_id == bank_id,
            TreasuryStatementLine.reference.in_(incoming_refs),
        ))).all())
    prior_fingerprints = {
        (item.reference, item.occurred_on, item.amount_minor, item.description)
        for item in other_rows if item.statement_id != payload.statement_id
    }
    inserted_fingerprints = set()
    inserted = 0
    for row in payload.rows:
        old = saved.get(row.row_number)
        if old is not None:
            if (old.occurred_on, old.amount_minor, old.description, old.reference) != (
                row.occurred_on, row.amount_minor, row.description, row.reference,
            ):
                raise HTTPException(409, f"Previously imported row {row.row_number} differs; use a new statement ID after review")
            continue
        if row.reference:
            fingerprint = (row.reference, row.occurred_on, row.amount_minor, row.description)
            if fingerprint in prior_fingerprints or fingerprint in inserted_fingerprints:
                raise HTTPException(409, "Possible duplicate bank transaction under another statement ID; review before import")
            inserted_fingerprints.add(fingerprint)
        session.add(TreasuryStatementLine(
            org_id=org, bank_account_id=bank_id, statement_id=payload.statement_id,
            imported_by=actor.id, **row.model_dump(),
        ))
        inserted += 1
    _audit(session, org, actor, "statement_imported", bank_id, statement_id=payload.statement_id, inserted=inserted)
    await session.commit()
    return {"inserted": inserted, "already_present": len(payload.rows) - inserted, "status": "unmatched_until_reviewed"}


@router.post("/requests/{request_id}/confirm", response_model=RequestOut)
async def confirm_request(request_id: uuid.UUID, payload: ConfirmRequest, request: Request, actor=Depends(require_ledger_role(LedgerRole.OWNER)), session: AsyncSession = Depends(get_ledger_db)):
    org = request.state.org_id
    item = await _request(session, org, request_id, lock=True)
    if item.status != "approved":
        raise HTTPException(409, "Only approved requests can be confirmed")
    if actor.id == item.requested_by:
        raise HTTPException(403, "Requestor cannot confirm their own payment")
    source = await session.scalar(select(TreasuryStatementLine).where(
        TreasuryStatementLine.org_id == org, TreasuryStatementLine.id == payload.source_line_id,
    ).with_for_update())
    if source is None or source.bank_account_id != item.source_bank_id or source.amount_minor != -item.amount_minor or source.match_status != "unmatched":
        raise HTTPException(422, "Unmatched source bank debit evidence of the exact requested amount is required")
    dest = None
    if item.kind == "funding":
        if not payload.destination_line_id or payload.destination_line_id == payload.source_line_id:
            raise HTTPException(422, "Funding requires separate destination bank credit evidence")
        dest = await session.scalar(select(TreasuryStatementLine).where(
            TreasuryStatementLine.org_id == org, TreasuryStatementLine.id == payload.destination_line_id,
        ).with_for_update())
        if dest is None or dest.bank_account_id != item.destination_bank_id or dest.amount_minor != item.amount_minor or dest.match_status != "unmatched":
            raise HTTPException(422, "Unmatched destination bank credit evidence of the exact amount is required")
    elif payload.destination_line_id:
        raise HTTPException(422, "An expense must not have a destination bank credit")
    source.match_status, source.request_id = "matched_request", item.id
    if dest:
        dest.match_status, dest.request_id = "matched_request", item.id
        item.destination_bank_line_id = dest.id
    item.bank_line_id = source.id
    item.status = "confirmed"
    item.confirmed_by = actor.id
    item.confirmed_at = utc_now()
    _audit(session, org, actor, "request_confirmed_from_bank_evidence", item.id, source_line=str(source.id), dest_line=str(dest.id) if dest else None)
    await session.commit()
    await session.refresh(item)
    return item


@router.get("/summary", response_model=TreasurySummary)
async def summary(request: Request, _actor=Depends(require_ledger_role(LedgerRole.VIEWER)), session: AsyncSession = Depends(get_ledger_db)):
    org = request.state.org_id
    banks = list((await session.scalars(select(TreasuryBankAccount).where(TreasuryBankAccount.org_id == org).order_by(TreasuryBankAccount.code))).all())
    async def count(where):
        return int(await session.scalar(select(func.count()).select_from(where[0]).where(*where[1])) or 0)
    pending = await count((TreasuryRequest, (TreasuryRequest.org_id == org, TreasuryRequest.status == "requested")))
    approved = await count((TreasuryRequest, (TreasuryRequest.org_id == org, TreasuryRequest.status == "approved")))
    unmatched = await count((TreasuryStatementLine, (TreasuryStatementLine.org_id == org, TreasuryStatementLine.match_status == "unmatched")))
    spent = int(await session.scalar(select(func.coalesce(func.sum(TreasuryRequest.amount_minor), 0)).where(
        TreasuryRequest.org_id == org, TreasuryRequest.kind == "expense", TreasuryRequest.status == "confirmed",
    )) or 0)
    return TreasurySummary(banks=banks, pending_requests=pending, approved_awaiting_bank_evidence=approved, unmatched_statement_lines=unmatched, confirmed_expenses_minor=spent)


@router.post("/statements/{line_id}/match-journal", response_model=StatementOut)
async def match_journal(line_id: uuid.UUID, payload: MatchJournal, request: Request, actor=Depends(require_ledger_role(LedgerRole.OPERATIONS)), session: AsyncSession = Depends(get_ledger_db)):
    """Review an actual bank statement line against a previously posted ledger entry.

    Does not post a new journal entry or overwrite the existing accounting system.
    A bank-to-bank transfer can have two lines pointing to one balanced journal entry.
    """
    org = request.state.org_id
    line = await session.scalar(select(TreasuryStatementLine).where(
        TreasuryStatementLine.org_id == org, TreasuryStatementLine.id == line_id,
    ).with_for_update())
    if line is None:
        raise HTTPException(404, "Statement row not found")
    if line.journal_entry_id:
        raise HTTPException(409, "This row is already matched to a journal entry")
    bank = await _bank(session, org, line.bank_account_id)
    if bank.ledger_account_id is None:
        raise HTTPException(409, "The bank must first be linked to a distinct accounting ledger account")
    entry = await session.scalar(select(JournalEntry).where(
        JournalEntry.org_id == org, JournalEntry.id == payload.journal_entry_id,
    ))
    if entry is None or entry.status.value != "posted":
        raise HTTPException(422, "A posted journal entry is required")
    legs = (await session.execute(select(
        func.coalesce(func.sum(JournalLine.debit_minor), 0),
        func.coalesce(func.sum(JournalLine.credit_minor), 0),
    ).where(
        JournalLine.org_id == org, JournalLine.entry_id == entry.id,
        JournalLine.account_id == bank.ledger_account_id,
    ))).one()
    net_minor = int(legs[0]) - int(legs[1])
    if net_minor != line.amount_minor:
        raise HTTPException(422, "This journal entry does not record the same signed bank movement")
    # For a customer payment/PSP posting, a single journal can carry several
    # other legs, but only an equal bank movement can substantiate this row.
    already = await session.scalar(select(TreasuryStatementLine.id).where(
        TreasuryStatementLine.org_id == org,
        TreasuryStatementLine.id != line.id,
        TreasuryStatementLine.bank_account_id == line.bank_account_id,
        TreasuryStatementLine.journal_entry_id == entry.id,
    ))
    if already:
        raise HTTPException(409, "This ledger bank movement was already assigned to another statement row")
    line.journal_entry_id = entry.id
    line.match_status = "matched_journal"
    _audit(session, org, actor, "bank_statement_matched_to_ledger", line.id, journal_entry_id=str(entry.id))
    await session.commit()
    await session.refresh(line)
    return line
