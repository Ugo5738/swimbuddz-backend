"""Internal service-to-service routes for the Ledger Service.

Emitters (payments_service, wallet_service, …) post journal entries here using a
service-role JWT (validated via libs.auth.require_service_role). Org context is
resolved by get_ledger_db (Phase 1: LEDGER_DEFAULT_ORG_ID). The route owns the
transaction boundary — it commits on success, rolls back on error.
"""

import uuid
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from libs.auth.dependencies import require_service_role
from libs.auth.models import AuthUser
from pydantic import BaseModel, Field
from services.ledger_service.app.db import get_ledger_raw_db
from services.ledger_service.app.deps import (
    get_ledger_db,
    set_org_rls_context,
)
from services.ledger_service.models import LedgerUser, Organization
from services.ledger_service.models.enums import (
    AccountingStandard,
    LedgerRole,
    OrgStatus,
)
from services.ledger_service.schemas.invoice import InvoiceCreate, InvoiceOut
from services.ledger_service.schemas.journal import (
    JournalEntryCreate,
    JournalEntryResult,
)
from services.ledger_service.schemas.reconciliation import (
    ExternalTransactionBatch,
    ReconciliationIntakeResult,
)
from services.ledger_service.schemas.reports import (
    CashPositionReport,
    DeferredRevenueReport,
    MarginReport,
    ProfitLossReport,
)
from services.ledger_service.services.accounts import (
    list_coa_templates,
    seed_chart_of_accounts,
)
from services.ledger_service.services.invoices import create_invoice
from services.ledger_service.services.periods import resolve_or_create_period
from services.ledger_service.services.posting import (
    PeriodClosedError,
    UnbalancedEntryError,
    UnresolvedAccountError,
    post_entry,
)
from services.ledger_service.services.reconciliation import (
    intake_external_transactions,
)
from services.ledger_service.services.reports import (
    cash_position,
    deferred_revenue,
    margin_by_domain,
    profit_loss,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/internal/ledger", tags=["ledger-internal"])


class ProvisionOrganizationRequest(BaseModel):
    """Onboard a company onto the multi-tenant detachable Ledger."""

    name: str = Field(min_length=2, max_length=200)
    legal_name: Optional[str] = Field(None, max_length=200)
    base_currency: str = Field(default="NGN", min_length=3, max_length=3)
    fiscal_year_start_month: int = Field(default=1, ge=1, le=12)
    accounting_standard: AccountingStandard = AccountingStandard.ACCRUAL
    tax_country: str = Field(default="NG", min_length=2, max_length=2)
    coa_template: str = Field(default="generic_smb", min_length=2, max_length=60)
    owner_email: str = Field(min_length=3, max_length=255)
    owner_auth_id: Optional[str] = Field(None, max_length=255)
    owner_display_name: Optional[str] = Field(None, max_length=200)
    org_metadata: Optional[dict] = None


class ProvisionOrganizationResponse(BaseModel):
    organization_id: uuid.UUID
    name: str
    base_currency: str
    coa_template: str
    accounts_created: int
    initial_period: str
    owner_user_id: uuid.UUID


@router.post(
    "/organizations",
    response_model=ProvisionOrganizationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def provision_organization(
    payload: ProvisionOrganizationRequest,
    _user: AuthUser = Depends(require_service_role),
    session: AsyncSession = Depends(get_ledger_raw_db),
) -> ProvisionOrganizationResponse:
    """Provision a brand-new tenant organization with its own CoA, period & owner."""
    if payload.coa_template not in list_coa_templates():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown coa_template {payload.coa_template!r}; choose from {list_coa_templates()}",
        )

    org = Organization(
        id=uuid.uuid4(),
        name=payload.name.strip(),
        legal_name=(payload.legal_name or "").strip() or None,
        base_currency=payload.base_currency.upper(),
        fiscal_year_start_month=payload.fiscal_year_start_month,
        accounting_standard=payload.accounting_standard,
        tax_country=payload.tax_country.upper(),
        status=OrgStatus.ACTIVE,
        org_metadata=payload.org_metadata,
    )
    # Bind RLS context to the new org id so subsequent inserts pass FORCE RLS.
    await set_org_rls_context(session, org.id)
    session.add(org)
    await session.flush()

    seed_result = await seed_chart_of_accounts(session, org.id, payload.coa_template)
    period = await resolve_or_create_period(session, org.id, date.today())

    owner = LedgerUser(
        org_id=org.id,
        email=payload.owner_email.strip().lower(),
        auth_id=(payload.owner_auth_id or "").strip() or None,
        display_name=(payload.owner_display_name or "").strip() or None,
        role=LedgerRole.OWNER,
    )
    session.add(owner)
    await session.commit()
    return ProvisionOrganizationResponse(
        organization_id=org.id,
        name=org.name,
        base_currency=org.base_currency,
        coa_template=payload.coa_template,
        accounts_created=seed_result["created"],
        initial_period=period.period_name,
        owner_user_id=owner.id,
    )


async def _activate_payload_org(
    session: AsyncSession,
    request: Request,
    payload_org_id: Optional[uuid.UUID],
) -> uuid.UUID:
    """Honor explicit ``payload.org_id`` when different from default org."""
    org_id = payload_org_id or request.state.org_id
    if payload_org_id is not None and payload_org_id != request.state.org_id:
        await set_org_rls_context(session, payload_org_id)
        org = await session.scalar(
            select(Organization).where(Organization.id == payload_org_id)
        )
        if org is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Target ledger organization not found",
            )
        if org.status != OrgStatus.ACTIVE:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Target ledger organization is {org.status.value}",
            )
        request.state.org_id = payload_org_id
    return org_id


@router.post(
    "/journal-entries",
    response_model=JournalEntryResult,
    status_code=status.HTTP_201_CREATED,
)
async def post_journal_entry(
    payload: JournalEntryCreate,
    request: Request,
    _user: AuthUser = Depends(require_service_role),
    session: AsyncSession = Depends(get_ledger_db),
) -> JournalEntryResult:
    """Post a balanced journal entry (idempotent). Service-role only.

    400 — unbalanced or unknown account_ref; 409 — period closed; 201 — posted
    (or idempotent replay, indicated by ``idempotent_replay`` in the body).
    """
    org_id = await _activate_payload_org(session, request, payload.org_id)
    try:
        result = await post_entry(
            session,
            org_id=org_id,
            payload=payload,
            posted_by_service=request.headers.get("X-Caller-Service"),
        )
    except (UnbalancedEntryError, UnresolvedAccountError) as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    except PeriodClosedError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc

    await session.commit()
    return result


@router.post(
    "/external-transactions",
    response_model=ReconciliationIntakeResult,
    status_code=status.HTTP_200_OK,
)
async def post_external_transactions(
    payload: ExternalTransactionBatch,
    request: Request,
    _user: AuthUser = Depends(require_service_role),
    session: AsyncSession = Depends(get_ledger_db),
) -> ReconciliationIntakeResult:
    """Ingest PSP settlement transactions and reconcile them against the books.

    Each transaction is upserted (idempotent per (org, psp, external_txn_id)) and
    matched against ``journal_lines.external_ref``; unmatched or amount-mismatched
    items open a reconciliation break. Service-role only (design §11.2).
    """
    org_id = await _activate_payload_org(session, request, payload.org_id)
    summary = await intake_external_transactions(session, org_id, payload.transactions)
    await session.commit()
    return ReconciliationIntakeResult(**summary)


@router.post(
    "/invoices",
    response_model=InvoiceOut,
    status_code=status.HTTP_201_CREATED,
)
async def post_invoice(
    payload: InvoiceCreate,
    request: Request,
    _user: AuthUser = Depends(require_service_role),
    session: AsyncSession = Depends(get_ledger_db),
) -> InvoiceOut:
    """Issue an invoice — allocate a gapless number + persist header/lines.

    Service-role only (design §13). Other services (corporate, payments) call this
    to issue an invoice for a deal/payment.
    """
    org_id = await _activate_payload_org(session, request, payload.org_id)
    result = await create_invoice(session, org_id, payload)
    await session.commit()
    return result


# ---- Internal reporting reads -------------------------------------------------
# Reporting-service needs finance truth without impersonating an admin finance
# user. These routes expose the same ledger report functions behind service-role
# auth; no accounting logic is duplicated here.


@router.get("/reports/profit-loss", response_model=ProfitLossReport)
async def internal_profit_loss(
    request: Request,
    from_date: date,
    to_date: date,
    group_by: str = "dimension_1",
    _user: AuthUser = Depends(require_service_role),
    session: AsyncSession = Depends(get_ledger_db),
) -> ProfitLossReport:
    try:
        return await profit_loss(
            session, request.state.org_id, from_date, to_date, group_by
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc


@router.get("/reports/margin", response_model=MarginReport)
async def internal_margin(
    request: Request,
    from_date: date,
    to_date: date,
    _user: AuthUser = Depends(require_service_role),
    session: AsyncSession = Depends(get_ledger_db),
) -> MarginReport:
    return await margin_by_domain(session, request.state.org_id, from_date, to_date)


@router.get("/reports/deferred-revenue", response_model=DeferredRevenueReport)
async def internal_deferred_revenue(
    request: Request,
    as_of: date,
    _user: AuthUser = Depends(require_service_role),
    session: AsyncSession = Depends(get_ledger_db),
) -> DeferredRevenueReport:
    return await deferred_revenue(session, request.state.org_id, as_of)


@router.get("/reports/cash-position", response_model=CashPositionReport)
async def internal_cash_position(
    request: Request,
    as_of: date,
    _user: AuthUser = Depends(require_service_role),
    session: AsyncSession = Depends(get_ledger_db),
) -> CashPositionReport:
    return await cash_position(session, request.state.org_id, as_of)
