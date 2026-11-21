# fmt: off
"""Controlled treasury evidence and approval workflow.

No endpoint triggers an external bank payment. A request can be approved only by
a different finance OWNER, and confirmed only with real imported bank evidence.
All queries are scoped by org_id; the bank remains the final payment authority.
"""
import uuid
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from libs.common.datetime_utils import utc_now
from services.ledger_service.app.deps import get_ledger_db, require_ledger_role
from services.ledger_service.models import (
    ChartOfAccounts, JournalEntry, JournalLine, CostCenter, Organization,
    TreasuryBankSnapshot, TreasuryForecastLine,
    TreasuryAuditEvent, TreasuryBankAccount, TreasuryBudget,
    TreasuryRequest, TreasuryStatementLine,
)
from services.ledger_service.models.enums import AccountType, LedgerRole, NormalBalance
from services.ledger_service.schemas.journal import JournalEntryCreate
from services.ledger_service.services.posting import post_entry, LedgerError
from services.ledger_service.schemas.treasury import (
    BankCreate, BankOut, BankUpdate, BudgetCreate, BudgetOut, ConfirmRequest, Decision,
    SnapshotCreate, ForecastCreate, ForecastOut, CashProjection, BankReconciliationRow,
    PostConfirmedRequest,
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
    # Keep the original SwimBuddz bank codes and ledger mappings intact.
    # All other bank accounts receive a unique, per-account code. A bank may
    # have multiple accounts at the same institution.
    if payload.code:
        existing = await session.scalar(select(TreasuryBankAccount).where(
            TreasuryBankAccount.org_id == org,
            TreasuryBankAccount.code == payload.code,
        ))
        if existing:
            raise HTTPException(409, "This legacy account is already registered.")
        bank_name = {"stanbic": "Stanbic IBTC", "moniepoint": "Moniepoint"}[payload.code]
        purpose = {"stanbic": "treasury", "moniepoint": "operations"}[payload.code]
        code = payload.code
    else:
        bank_name = payload.bank_name
        purpose = payload.purpose
        code = "acct_" + uuid.uuid4().hex
    bank = TreasuryBankAccount(
        org_id=org, code=code, bank_name=bank_name,
        purpose=purpose, last_four=payload.last_four,
    )
    session.add(bank)
    await session.flush()
    _audit(session, org, actor, "bank_created", bank.id, code=bank.code)
    await session.commit()
    await session.refresh(bank)
    return bank


@router.patch("/banks/{bank_id}", response_model=BankOut)
async def update_bank_details(bank_id: uuid.UUID, payload: BankUpdate, request: Request, actor=Depends(require_ledger_role(LedgerRole.OWNER)), session: AsyncSession = Depends(get_ledger_db)):
    """Complete masked identifying details of an existing registered account.

    This never changes bank ownership, ledger mapping, balances or bank feeds.
    """
    org = request.state.org_id
    bank = await _bank(session, org, bank_id)
    if bank.last_four != payload.last_four:
        bank.last_four = payload.last_four
        _audit(session, org, actor, "bank_details_updated", bank.id, last_four=bank.last_four)
        await session.commit()
        await session.refresh(bank)
    return bank


@router.get("/expense-ledger-options")
async def expense_ledger_options(request: Request, _actor=Depends(require_ledger_role(LedgerRole.VIEWER)), session: AsyncSession = Depends(get_ledger_db)):
    org = request.state.org_id
    accounts = (await session.scalars(select(ChartOfAccounts).where(
        ChartOfAccounts.org_id == org, ChartOfAccounts.is_active.is_(True),
        ChartOfAccounts.type == AccountType.EXPENSE,
    ).order_by(ChartOfAccounts.code))).all()
    return [{"code": a.code, "name": a.name} for a in accounts
            if a.code.startswith(("5", "6")) and a.code not in ("5000", "6000")]


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
    legacy_maps = {"stanbic": "bank_operating_ngn", "moniepoint": "bank_moniepoint_ngn"}
    expected = legacy_maps.get(bank.code, "bank_account_" + bank.id.hex)
    if not account.account_metadata or account.account_metadata.get("maps_to") != expected:
        raise HTTPException(422, "Select the dedicated ledger account for this bank; do not mix bank balances")
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


@router.post("/banks/{bank_id}/create-ledger", response_model=BankOut)
async def create_bank_ledger(bank_id: uuid.UUID, request: Request, actor=Depends(require_ledger_role(LedgerRole.OWNER)), session: AsyncSession = Depends(get_ledger_db)):
    """Create and link a distinct NGN asset account for a newly registered bank.

    Never overwrite or remap the existing Stanbic and Moniepoint accounts.
    No bank connection, balance assertion or journal posting is implied.
    """
    org = request.state.org_id
    bank = await _bank(session, org, bank_id)
    # Serialize account creation for this bank across concurrent requests.
    bank = await session.scalar(select(TreasuryBankAccount).where(
        TreasuryBankAccount.org_id == org, TreasuryBankAccount.id == bank_id,
    ).with_for_update())
    if bank.ledger_account_id is not None:
        return bank
    if bank.code in ("stanbic", "moniepoint"):
        raise HTTPException(422, "Use the legacy ledger mapping workflow for this account")

    stable_code = "11-" + bank.id.hex[:16]
    account = await session.scalar(select(ChartOfAccounts).where(
        ChartOfAccounts.org_id == org, ChartOfAccounts.code == stable_code,
    ))
    if account is None:
        base = await session.scalar(select(ChartOfAccounts).where(
            ChartOfAccounts.org_id == org, ChartOfAccounts.code == "1110",
        ))
        account = ChartOfAccounts(
            org_id=org, code=stable_code, name="Bank — " + bank.bank_name + " (NGN)",
            type=AccountType.ASSET, normal_balance=NormalBalance.DEBIT,
            parent_id=base.parent_id if base else None,
            is_active=True, is_system=False,
            account_metadata={"maps_to": "bank_account_" + bank.id.hex},
        )
        session.add(account)
        await session.flush()
    elif not account.account_metadata or account.account_metadata.get("maps_to") != "bank_account_" + bank.id.hex:
        raise HTTPException(409, "Bank ledger account code already belongs to another purpose")
    bank.ledger_account_id = account.id
    _audit(session, org, actor, "bank_ledger_created", bank.id, account_code=account.code)
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
    # Serialize budget creation within the organization so two simultaneous
    # requests cannot both pass the overlapping-date check.
    await session.scalar(select(Organization.id).where(
        Organization.id == org,
    ).with_for_update())
    overlapping = await session.scalar(select(TreasuryBudget.id).where(
        TreasuryBudget.org_id == org,
        TreasuryBudget.kind == payload.kind,
        TreasuryBudget.category == payload.category,
        TreasuryBudget.starts_on <= payload.ends_on,
        TreasuryBudget.ends_on >= payload.starts_on,
    ).limit(1))
    if overlapping is not None:
        raise HTTPException(
            409, "A budget already covers this kind, category and date range"
        )
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


@router.get("/budgets/utilization")
async def budget_utilization(request: Request, _actor=Depends(require_ledger_role(LedgerRole.VIEWER)), session: AsyncSession = Depends(get_ledger_db)):
    org = request.state.org_id
    budgets = (await session.scalars(select(TreasuryBudget).where(
        TreasuryBudget.org_id == org,
    ).order_by(TreasuryBudget.starts_on.desc()).limit(100))).all()
    grouped = (await session.execute(select(
        TreasuryRequest.budget_id, TreasuryRequest.status,
        func.coalesce(func.sum(TreasuryRequest.amount_minor), 0),
    ).where(
        TreasuryRequest.org_id == org, TreasuryRequest.budget_id.is_not(None),
        TreasuryRequest.status.in_(("approved", "confirmed")),
    ).group_by(TreasuryRequest.budget_id, TreasuryRequest.status))).all()
    amounts = {(budget_id, status): int(amount) for budget_id, status, amount in grouped}
    result = []
    for budget in budgets:
        committed = amounts.get((budget.id, "approved"), 0)
        confirmed = amounts.get((budget.id, "confirmed"), 0)
        result.append({
            "id": str(budget.id), "kind": budget.kind,
            "name": budget.name, "category": budget.category,
            "limit_minor": budget.amount_minor,
            "committed_minor": committed,
            "confirmed_minor": confirmed,
            "remaining_minor": budget.amount_minor - committed - confirmed,
            "starts_on": str(budget.starts_on), "ends_on": str(budget.ends_on),
        })
    return result


@router.post("/requests", response_model=RequestOut, status_code=201)
async def create_request(payload: RequestCreate, request: Request, actor=Depends(require_ledger_role(LedgerRole.OPERATIONS)), session: AsyncSession = Depends(get_ledger_db)):
    org = request.state.org_id
    source = await _bank(session, org, payload.source_bank_id)
    if payload.kind == "funding":
        dest = await _bank(session, org, payload.destination_bank_id)
        if source.purpose != "treasury" or dest.purpose != "operations":
            raise HTTPException(422, "Funding requests must move from treasury to operations")
    if payload.cost_center_code:
        center = await session.scalar(select(CostCenter.id).where(
            CostCenter.org_id == org, CostCenter.code == payload.cost_center_code,
            CostCenter.is_active.is_(True),
        ))
        if center is None:
            raise HTTPException(422, "Unknown or inactive cost centre")
    if payload.budget_id is not None:
        budget = await session.scalar(select(TreasuryBudget).where(TreasuryBudget.org_id == org, TreasuryBudget.id == payload.budget_id))
        if budget is None or budget.category != payload.category or budget.kind != payload.kind:
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
    if payload.action == "approve" and item.budget_id is None and not (payload.reason or "").strip():
        raise HTTPException(422, "An unbudgeted commitment requires a written owner exception reason")
    if payload.action == "approve" and item.budget_id is not None:
        budget = await session.scalar(select(TreasuryBudget).where(TreasuryBudget.org_id == org, TreasuryBudget.id == item.budget_id).with_for_update())
        if budget is None:
            raise HTTPException(409, "Budget was removed")
        if budget.kind != item.kind or budget.category != item.category:
            raise HTTPException(409, "Request budget category or kind no longer matches")
        if not (budget.starts_on <= date.today() <= budget.ends_on):
            raise HTTPException(409, "Budget period has expired; issue a new authorisation")
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
    item.approval_note = (payload.reason or "").strip() if item.status == "approved" else None
    _audit(session, org, actor, "request_" + item.status, item.id, reason=item.rejected_reason or item.approval_note)
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
    if item.kind == "expense" and not (item.evidence_reference or item.invoice_reference):
        raise HTTPException(422, "Expense confirmation requires receipt or invoice evidence reference")
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
async def match_journal(line_id: uuid.UUID, payload: MatchJournal, request: Request, actor=Depends(require_ledger_role(LedgerRole.OWNER)), session: AsyncSession = Depends(get_ledger_db)):
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

@router.post("/banks/{bank_id}/snapshot", response_model=BankOut)
async def attest_bank_snapshot(bank_id: uuid.UUID, payload: SnapshotCreate, request: Request, actor=Depends(require_ledger_role(LedgerRole.OWNER)), session: AsyncSession = Depends(get_ledger_db)):
    """Owner records a dated verified statement closing balance. No pretend live API."""
    org = request.state.org_id
    bank = await session.scalar(select(TreasuryBankAccount).where(
        TreasuryBankAccount.org_id == org, TreasuryBankAccount.id == bank_id,
    ).with_for_update())
    if bank is None:
        raise HTTPException(404, "Bank not found")
    if payload.as_of > date.today():
        raise HTTPException(422, "Cannot attest a future bank balance")
    newest = await session.scalar(select(func.max(TreasuryBankSnapshot.as_of)).where(
        TreasuryBankSnapshot.org_id == org, TreasuryBankSnapshot.bank_account_id == bank_id,
    ))
    if newest and payload.as_of <= newest:
        raise HTTPException(409, "A same-day or later verified snapshot exists; audit before correction")
    session.add(TreasuryBankSnapshot(
        org_id=org, bank_account_id=bank_id, as_of=payload.as_of,
        balance_minor=payload.balance_minor, evidence_reference=payload.evidence_reference,
        attested_by=actor.id,
    ))
    bank.statement_balance_minor = payload.balance_minor
    bank.statement_as_of = payload.as_of
    bank.statement_verified_at = utc_now()
    bank.statement_evidence = payload.evidence_reference
    _audit(session, org, actor, "statement_balance_attested", bank_id, as_of=str(payload.as_of), reference=payload.evidence_reference)
    await session.commit()
    await session.refresh(bank)
    return bank


@router.get("/reconciliation/banks", response_model=list[BankReconciliationRow])
async def reconcile_banks(request: Request, as_of: date | None = None, _actor=Depends(require_ledger_role(LedgerRole.VIEWER)), session: AsyncSession = Depends(get_ledger_db)):
    """An as-of account reconciliation; mismatched statement dates are flagged."""
    org = request.state.org_id
    end = as_of or date.today()
    banks = list((await session.scalars(select(TreasuryBankAccount).where(
        TreasuryBankAccount.org_id == org,
    ))).all())
    result = []
    for bank in banks:
        snap = await session.scalar(select(TreasuryBankSnapshot).where(
            TreasuryBankSnapshot.org_id == org, TreasuryBankSnapshot.bank_account_id == bank.id,
            TreasuryBankSnapshot.as_of <= end,
        ).order_by(TreasuryBankSnapshot.as_of.desc()).limit(1))
        counts = (await session.execute(select(
            TreasuryStatementLine.match_status, func.count(TreasuryStatementLine.id),
        ).where(
            TreasuryStatementLine.org_id == org,
            TreasuryStatementLine.bank_account_id == bank.id,
            TreasuryStatementLine.occurred_on <= end,
        ).group_by(TreasuryStatementLine.match_status))).all()
        match_count = sum(int(n) for status, n in counts if status == "matched_journal")
        unmatch_count = sum(int(n) for status, n in counts if status != "matched_journal")
        # Do not compare a snapshot from last week to today's ledger as if same date.
        comparable_ledger = None
        if snap is not None and bank.ledger_account_id:
            comparable_ledger = int((await session.scalar(select(
                func.coalesce(func.sum(JournalLine.debit_minor - JournalLine.credit_minor), 0)
            ).select_from(JournalLine).join(JournalEntry, JournalLine.entry_id == JournalEntry.id).where(
                JournalLine.org_id == org, JournalLine.account_id == bank.ledger_account_id,
                JournalEntry.entry_date <= snap.as_of,
            ))) or 0)
        difference = snap.balance_minor - comparable_ledger if snap and comparable_ledger is not None else None
        result.append(BankReconciliationRow(
            bank_id=bank.id, code=bank.code,
            latest_statement_balance_minor=snap.balance_minor if snap else None,
            latest_statement_date=snap.as_of if snap else None,
            ledger_balance_minor=comparable_ledger,
            difference_minor=difference, matched_rows=match_count,
            unmatched_rows=unmatch_count,
            status=("not_configured" if not bank.ledger_account_id else
                    "needs_snapshot" if not snap else
                    "balanced" if difference == 0 and unmatch_count == 0 else "needs_reconciliation"),
        ))
    return result


@router.post("/forecasts", response_model=ForecastOut, status_code=201)
async def create_forecast(payload: ForecastCreate, request: Request, actor=Depends(require_ledger_role(LedgerRole.OPERATIONS)), session: AsyncSession = Depends(get_ledger_db)):
    org = request.state.org_id
    if payload.effective_on < date.today():
        raise HTTPException(422, "Forecast assumptions must be dated today or later")
    line = TreasuryForecastLine(org_id=org, created_by=actor.id, **payload.model_dump())
    session.add(line)
    await session.flush()
    _audit(session, org, actor, "forecast_assumption_created", line.id, amount_minor=line.amount_minor)
    await session.commit()
    await session.refresh(line)
    return line


@router.get("/forecasts", response_model=list[ForecastOut])
async def list_forecasts(request: Request, _actor=Depends(require_ledger_role(LedgerRole.VIEWER)), session: AsyncSession = Depends(get_ledger_db)):
    return list((await session.scalars(select(TreasuryForecastLine).where(
        TreasuryForecastLine.org_id == request.state.org_id,
        TreasuryForecastLine.cancelled_at.is_(None),
    ).order_by(TreasuryForecastLine.effective_on).limit(300))).all())


@router.get("/cash-projection", response_model=CashProjection)
async def cash_projection(request: Request, scenario: str = "base", days: int = 30, _actor=Depends(require_ledger_role(LedgerRole.VIEWER)), session: AsyncSession = Depends(get_ledger_db)):
    if scenario not in ("base", "optimistic", "conservative") or not 1 <= days <= 365:
        raise HTTPException(422, "Invalid scenario or forecasting horizon")
    org = request.state.org_id
    today = date.today()
    lines = (await session.scalars(select(TreasuryForecastLine).where(
        TreasuryForecastLine.org_id == org,
        TreasuryForecastLine.cancelled_at.is_(None),
        TreasuryForecastLine.effective_on >= today,
        TreasuryForecastLine.effective_on <= today + timedelta(days=days),
        TreasuryForecastLine.scenario == scenario,
    ))).all()
    incoming = sum(forecast_line.amount_minor for forecast_line in lines if forecast_line.direction == "inflow")
    outgoing = sum(forecast_line.amount_minor for forecast_line in lines if forecast_line.direction == "outflow")
    banks = (await session.scalars(select(TreasuryBankAccount).where(
        TreasuryBankAccount.org_id == org,
    ))).all()
    # Starting cash needs two comparable, explicitly attested balances.
    snapshots = [b for b in banks if b.statement_balance_minor is not None and b.statement_as_of is not None]
    starting = sum(b.statement_balance_minor for b in snapshots) if len(snapshots) == 2 and len(banks) == 2 and len({b.statement_as_of for b in snapshots}) == 1 else None
    approved_expenses = int((await session.scalar(select(func.coalesce(func.sum(TreasuryRequest.amount_minor), 0)).where(
        TreasuryRequest.org_id == org, TreasuryRequest.kind == "expense", TreasuryRequest.status == "approved",
        ((TreasuryRequest.service_on.is_(None)) | (TreasuryRequest.service_on <= today + timedelta(days=days))),
    ))) or 0)
    # Pending approved expenses are deducted unless explicitly in forecast lines;
    # an independent expense budget is a ceiling, not an expense or cash flow.
    projected = starting + incoming - outgoing - approved_expenses if starting is not None else None
    return CashProjection(
        scenario=scenario, latest_bank_snapshot_minor=starting,
        latest_bank_snapshot_date=snapshots[0].statement_as_of if starting is not None else None,
        planned_inflows_minor=incoming, planned_outflows_minor=outgoing,
        projected_ending_minor=projected, pending_approved_expenses_minor=approved_expenses,
        caveat="Planning model, not live bank data. Only approved expenses due within the horizon are counted as commitments; ensure planned outflows exclude those requests to prevent double counting.",
    )


@router.post("/requests/{request_id}/post-ledger", response_model=RequestOut)
async def post_confirmed_to_ledger(request_id: uuid.UUID, payload: PostConfirmedRequest, request: Request, actor=Depends(require_ledger_role(LedgerRole.OWNER)), session: AsyncSession = Depends(get_ledger_db)):
    """Controlled automatic double-entry posting; never executes a bank transfer."""
    org = request.state.org_id
    item = await _request(session, org, request_id, lock=True)
    if item.status != "confirmed":
        raise HTTPException(409, "Only bank-evidence-confirmed requests can post")
    if actor.id == item.requested_by:
        raise HTTPException(403, "Requestor cannot book own transfer")
    if not payload.acknowledgement:
        raise HTTPException(422, "Owner must acknowledge verified original bank evidence")
    if item.journal_entry_id:
        raise HTTPException(409, "Request already posted to ledger")
    source = await session.scalar(select(TreasuryStatementLine).where(
        TreasuryStatementLine.org_id == org, TreasuryStatementLine.id == item.bank_line_id,
    ))
    bank = await _bank(session, org, item.source_bank_id)
    if not source or source.request_id != item.id or source.amount_minor != -item.amount_minor or not bank.ledger_account_id:
        raise HTTPException(409, "Source bank or debit evidence not valid/linked")
    source_acct = await session.scalar(select(ChartOfAccounts).where(
        ChartOfAccounts.org_id == org, ChartOfAccounts.id == bank.ledger_account_id,
    ))
    if not source_acct:
        raise HTTPException(409, "Source ledger not linked")
    destination = None
    dest = None
    expense_acct = None
    if item.kind == "funding":
        dest = await session.scalar(select(TreasuryStatementLine).where(
            TreasuryStatementLine.org_id == org, TreasuryStatementLine.id == item.destination_bank_line_id,
        ))
        destination = await _bank(session, org, item.destination_bank_id)
        if not dest or dest.request_id != item.id or dest.amount_minor != item.amount_minor or not destination.ledger_account_id:
            raise HTTPException(409, "Destination credit evidence is missing")
        dest_acct = await session.scalar(select(ChartOfAccounts).where(
            ChartOfAccounts.org_id == org, ChartOfAccounts.id == destination.ledger_account_id,
        ))
        if not dest_acct:
            raise HTTPException(409, "Destination ledger not linked")
        if payload.expense_account_code:
            raise HTTPException(422, "Interbank funding is not a business expense")
        debit_ref = dest_acct.code
    else:
        if not item.evidence_reference and not item.invoice_reference:
            raise HTTPException(409, "Expense documentation is missing")
        if not payload.expense_account_code:
            raise HTTPException(422, "Expense posting requires the reviewed expense account code")
        expense_acct = await session.scalar(select(ChartOfAccounts).where(
            ChartOfAccounts.org_id == org,
            ChartOfAccounts.code == payload.expense_account_code,
            ChartOfAccounts.type == AccountType.EXPENSE,
            ChartOfAccounts.is_active.is_(True),
        ))
        if not expense_acct:
            raise HTTPException(422, "Invalid or inactive expense account")
        if expense_acct.code in ("5300", "5400", "6100") or item.category in ("coach_payout", "refund", "paystack_fee", "payment_processing"):
            raise HTTPException(409, "This cost is handled by an existing payout/refund/PSP ledger workflow; reconcile its entry instead")
        debit_ref = expense_acct.code
    if source.journal_entry_id or (dest and dest.journal_entry_id):
        raise HTTPException(409, "Evidence already reconciled to another journal; link instead of posting twice")
    if not source.reference:
        raise HTTPException(409, "Automated posting needs a bank transaction reference; otherwise reconcile manually")
    possible_duplicate = await session.scalar(select(JournalEntry.id).select_from(JournalEntry).join(
        JournalLine, JournalLine.entry_id == JournalEntry.id,
    ).where(
        JournalEntry.org_id == org, JournalLine.org_id == org,
        JournalLine.account_id == bank.ledger_account_id,
        JournalLine.external_ref == source.reference,
        JournalLine.credit_minor - JournalLine.debit_minor == item.amount_minor,
    ).limit(1))
    if possible_duplicate:
        raise HTTPException(409, "Matching bank ledger movement already exists; link the entry instead of double-posting")

    # Force a separate fiscal review of older, already-posted payout entries:
    # the OWNER acknowledges there is no duplicate entry in the existing ledger.
    lines = [
        dict(account_ref=debit_ref, debit=item.amount_minor, credit=0, currency="NGN",
             dimension_1=item.programme or None, cost_center=item.cost_center_code,
             external_ref=source.reference, description=item.purpose),
        dict(account_ref=source_acct.code, debit=0, credit=item.amount_minor, currency="NGN",
             dimension_1=item.programme or None, cost_center=item.cost_center_code,
             external_ref=source.reference, description=item.purpose),
    ]
    entry_payload = JournalEntryCreate(
        idempotency_key=f"treasury:request:{item.id}",
        entry_date=source.occurred_on, description=item.purpose,
        source_service="ledger", source_type="treasury_expense" if item.kind == "expense" else "treasury_internal_funding",
        source_id=str(item.id),
        metadata={"request_id": str(item.id), "verified_bank_statement_id": source.statement_id},
        lines=lines,
    )
    try:
        entry = await post_entry(session, org_id=org, payload=entry_payload,
                                 posted_by_user_id=actor.id, posted_by_service="treasury")
    except LedgerError as exc:
        raise HTTPException(409, str(exc)) from exc
    item.journal_entry_id = entry.entry_id
    item.ledger_posted_at = utc_now()
    source.journal_entry_id = entry.entry_id
    source.match_status = "matched_journal"
    if dest:
        dest.journal_entry_id = entry.entry_id
        dest.match_status = "matched_journal"
    _audit(session, org, actor, "request_posted_to_ledger", item.id, entry_id=str(entry.entry_id), replay=entry.idempotent_replay)
    await session.commit()
    await session.refresh(item)
    return item

@router.post("/requests/{request_id}/link-existing-ledger", response_model=RequestOut)
async def link_existing_request_ledger(request_id: uuid.UUID, payload: MatchJournal, request: Request, actor=Depends(require_ledger_role(LedgerRole.OWNER)), session: AsyncSession = Depends(get_ledger_db)):
    """Use an already posted journal when the legacy payments/payout emitters booked it."""
    org = request.state.org_id
    item = await _request(session, org, request_id, lock=True)
    if item.status != "confirmed" or item.journal_entry_id:
        raise HTTPException(409, "Only previously unlinked confirmed requests can be reconciled")
    if actor.id == item.requested_by:
        raise HTTPException(403, "Requestor cannot independently certify their own reconciliation")
    entry = await session.scalar(select(JournalEntry).where(
        JournalEntry.org_id == org, JournalEntry.id == payload.journal_entry_id,
    ))
    if not entry or entry.status.value != "posted":
        raise HTTPException(422, "Journal entry must be posted within the same organization")
    src = await session.scalar(select(TreasuryStatementLine).where(
        TreasuryStatementLine.org_id == org, TreasuryStatementLine.id == item.bank_line_id,
    ))
    bank = await _bank(session, org, item.source_bank_id)
    if not src or not bank.ledger_account_id or src.journal_entry_id != entry.id:
        raise HTTPException(409, "Source bank statement must first be matched to this journal")
    if item.kind == "funding":
        dest = await session.scalar(select(TreasuryStatementLine).where(
            TreasuryStatementLine.org_id == org, TreasuryStatementLine.id == item.destination_bank_line_id,
        ))
        if not dest or dest.journal_entry_id != entry.id:
            raise HTTPException(409, "Both bank statement legs must match this same transfer journal")
    item.journal_entry_id = entry.id
    item.ledger_posted_at = entry.posted_at or utc_now()
    _audit(session, org, actor, "request_linked_to_existing_journal", item.id, entry_id=str(entry.id))
    await session.commit()
    await session.refresh(item)
    return item

@router.delete("/forecasts/{line_id}", status_code=204)
async def cancel_forecast(line_id: uuid.UUID, request: Request, actor=Depends(require_ledger_role(LedgerRole.OWNER)), session: AsyncSession = Depends(get_ledger_db)):
    """Keep audit history of a cancelled planning assumption."""
    org = request.state.org_id
    item = await session.scalar(select(TreasuryForecastLine).where(
        TreasuryForecastLine.id == line_id, TreasuryForecastLine.org_id == org,
    ).with_for_update())
    if item is None:
        raise HTTPException(404, "Forecast assumption not found")
    if item.cancelled_at:
        raise HTTPException(409, "Forecast assumption already cancelled")
    item.cancelled_at = utc_now()
    item.cancelled_by = actor.id
    _audit(session, org, actor, "forecast_assumption_cancelled", item.id)
    await session.commit()
    return None
