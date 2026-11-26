"""Dedicated procurement grants. A JWT role label alone never grants finance access."""

import uuid
from dataclasses import dataclass

import httpx
from fastapi import Depends, HTTPException, Request
from libs.auth.dependencies import get_current_user
from libs.auth.models import AuthUser
from libs.common.config import get_settings
from libs.db.session import get_async_db
from services.store_service.models.procurement import StoreProcurementGrant
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class FinanceOwner:
    id: uuid.UUID
    auth_id: str


async def procurement_owner(
    request: Request,
    user: AuthUser = Depends(get_current_user),
) -> FinanceOwner:
    """Ask Ledger about the actual caller; never import its service package.

    Forward the caller's token rather than a service-role token. Ledger keeps
    ownership of finance membership, revocation and email-linked identities.
    """
    authorization = request.headers.get("authorization")
    if not authorization:
        raise HTTPException(401, "Sign in to verify Finance Owner access")
    headers = {"Authorization": authorization}
    org_header = request.headers.get("x-ledger-org-id")
    if org_header:
        headers["X-Ledger-Org-Id"] = org_header
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{get_settings().LEDGER_SERVICE_URL}/admin/finance/users/me",
                headers=headers,
            )
    except httpx.RequestError as exc:
        raise HTTPException(503, "Finance authorization is unavailable") from exc
    if response.status_code in {401, 403}:
        raise HTTPException(response.status_code, "Finance Owner access required")
    if response.status_code != 200:
        raise HTTPException(503, "Finance authorization is unavailable")
    try:
        membership = response.json()
        if not isinstance(membership, dict):
            raise ValueError("Invalid finance membership response")
        if membership.get("role") != "owner" or membership.get("deactivated_at"):
            raise HTTPException(403, "Finance Owner access required")
        owner_id = uuid.UUID(membership["id"])
        auth_id = membership.get("auth_id") or user.user_id
        if not isinstance(auth_id, str) or not auth_id:
            raise ValueError("Invalid finance identity")
    except (KeyError, ValueError, TypeError) as exc:
        raise HTTPException(503, "Finance authorization is unavailable") from exc
    return FinanceOwner(id=owner_id, auth_id=auth_id)


async def verify_ledger_journal_entry(
    request: Request,
    journal_entry_id: uuid.UUID,
) -> dict:
    """Confirm a referenced Ledger journal entry exists and is posted via HTTP.

    Never imports ledger_service; forwards the authenticated owner's bearer token
    (and optional X-Ledger-Org-Id) to Ledger's own journal-entry read endpoint.
    """
    authorization = request.headers.get("authorization")
    if not authorization:
        raise HTTPException(401, "Sign in to verify linked ledger journal entry")
    headers = {"Authorization": authorization}
    org_header = request.headers.get("x-ledger-org-id")
    if org_header:
        headers["X-Ledger-Org-Id"] = org_header
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{get_settings().LEDGER_SERVICE_URL}/admin/finance/journal-entries/{journal_entry_id}",
                headers=headers,
            )
    except httpx.RequestError as exc:
        raise HTTPException(
            503, "Finance service unavailable while verifying journal entry"
        ) from exc
    if response.status_code == 404:
        raise HTTPException(
            422, "Referenced ledger journal entry was not found in your organization"
        )
    if response.status_code in {401, 403}:
        raise HTTPException(
            response.status_code, "Not authorized to verify this ledger journal entry"
        )
    if response.status_code != 200:
        raise HTTPException(
            503, "Finance service unavailable while verifying journal entry"
        )
    data = response.json()
    if not isinstance(data, dict) or data.get("status") != "posted":
        raise HTTPException(
            422, "Referenced ledger journal entry must be in posted status"
        )
    return data


async def procurement_reader(
    user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> AuthUser:
    # The existing system administrator remains permitted for operational
    # continuity; the separate procurement grant never grants admin authority.
    if user.role == "service_role" or user.has_role("admin"):
        return user
    grant = await db.scalar(
        select(StoreProcurementGrant).where(
            StoreProcurementGrant.auth_id == user.user_id,
            StoreProcurementGrant.active.is_(True),
        )
    )
    if grant is None:
        raise HTTPException(status_code=403, detail="Store procurement access required")
    return user


async def procurement_writer(
    user: AuthUser = Depends(procurement_reader),
    db: AsyncSession = Depends(get_async_db),
) -> AuthUser:
    if user.role == "service_role" or user.has_role("admin"):
        return user
    grant = await db.scalar(
        select(StoreProcurementGrant).where(
            StoreProcurementGrant.auth_id == user.user_id,
            StoreProcurementGrant.role == "procurement_officer",
            StoreProcurementGrant.active.is_(True),
        )
    )
    if grant is None:
        raise HTTPException(
            status_code=403, detail="Store procurement write permission required"
        )
    return user
