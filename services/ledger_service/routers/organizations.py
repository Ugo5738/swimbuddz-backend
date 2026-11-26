"""Organization profile and multi-tenant context endpoints for Ledger Service.

Allows any company onboarded onto the detachable Ledger to inspect and configure
its own organization profile (base currency, tax registration, protected
expense categories, reporting dimension labels) and list organizations the
authenticated user belongs to.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from libs.auth.dependencies import get_current_user
from libs.auth.models import AuthUser
from pydantic import BaseModel, ConfigDict, Field
from services.ledger_service.app.db import get_ledger_raw_db
from services.ledger_service.app.deps import get_ledger_db, require_ledger_role
from services.ledger_service.models import LedgerUser, Organization
from services.ledger_service.models.enums import (
    AccountingStandard,
    LedgerRole,
    OrgStatus,
)
from services.ledger_service.services.accounts import list_coa_templates
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/admin/finance/organizations", tags=["ledger-organizations"])


class OrganizationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    legal_name: Optional[str] = None
    base_currency: str
    fiscal_year_start_month: int
    accounting_standard: AccountingStandard
    tax_country: str
    vat_number: Optional[str] = None
    firs_taxpayer_id: Optional[str] = None
    status: OrgStatus
    org_metadata: Optional[dict] = None
    created_at: datetime
    updated_at: datetime


class CurrentOrganizationResponse(BaseModel):
    organization: OrganizationOut
    available_coa_templates: list[str]


class OrganizationUpdate(BaseModel):
    legal_name: Optional[str] = Field(None, max_length=200)
    tax_country: Optional[str] = Field(None, min_length=2, max_length=2)
    vat_number: Optional[str] = Field(None, max_length=100)
    firs_taxpayer_id: Optional[str] = Field(None, max_length=100)
    org_metadata: Optional[dict] = None


class UserOrganizationMembership(BaseModel):
    organization_id: uuid.UUID
    name: str
    legal_name: Optional[str] = None
    base_currency: str
    role: LedgerRole


@router.get("", response_model=list[UserOrganizationMembership])
async def list_my_organizations(
    user: AuthUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_ledger_raw_db),
) -> list[UserOrganizationMembership]:
    """List active Ledger organizations the authenticated user belongs to.

    Supports multi-company switching via ``X-Ledger-Org-Id``. Note: when
    running under a strict ``NOBYPASSRLS`` role without a cross-org lookup view,
    clients can also inspect ``/current`` directly with ``X-Ledger-Org-Id``.
    """
    match = [LedgerUser.auth_id == user.user_id]
    if user.email:
        match.append(LedgerUser.email == str(user.email))

    rows = (
        await session.execute(
            select(Organization, LedgerUser.role)
            .join(LedgerUser, LedgerUser.org_id == Organization.id)
            .where(
                Organization.status == OrgStatus.ACTIVE,
                LedgerUser.deactivated_at.is_(None),
                or_(*match),
            )
            .order_by(Organization.name)
        )
    ).all()

    return [
        UserOrganizationMembership(
            organization_id=org.id,
            name=org.name,
            legal_name=org.legal_name,
            base_currency=org.base_currency,
            role=role,
        )
        for org, role in rows
    ]


@router.get("/current", response_model=CurrentOrganizationResponse)
async def get_current_organization(
    request: Request,
    _actor=Depends(require_ledger_role(LedgerRole.VIEWER)),
    session: AsyncSession = Depends(get_ledger_db),
) -> CurrentOrganizationResponse:
    """Return the active organization profile for the current request context."""
    org = await session.scalar(
        select(Organization).where(Organization.id == request.state.org_id)
    )
    if org is None:
        raise HTTPException(404, "Organization not found")
    return CurrentOrganizationResponse(
        organization=OrganizationOut.model_validate(org),
        available_coa_templates=list_coa_templates(),
    )


@router.patch("/current", response_model=OrganizationOut)
async def update_current_organization(
    payload: OrganizationUpdate,
    request: Request,
    _actor=Depends(require_ledger_role(LedgerRole.OWNER)),
    session: AsyncSession = Depends(get_ledger_db),
) -> OrganizationOut:
    """Owner-only update of organization tax/treasury metadata."""
    org = await session.scalar(
        select(Organization)
        .where(Organization.id == request.state.org_id)
        .with_for_update()
    )
    if org is None:
        raise HTTPException(404, "Organization not found")

    if payload.legal_name is not None:
        org.legal_name = payload.legal_name.strip() or None
    if payload.tax_country is not None:
        org.tax_country = payload.tax_country.upper()
    if payload.vat_number is not None:
        org.vat_number = payload.vat_number.strip() or None
    if payload.firs_taxpayer_id is not None:
        org.firs_taxpayer_id = payload.firs_taxpayer_id.strip() or None
    if payload.org_metadata is not None:
        merged = dict(org.org_metadata or {})
        merged.update(payload.org_metadata)
        org.org_metadata = merged

    await session.commit()
    await session.refresh(org)
    return OrganizationOut.model_validate(org)
