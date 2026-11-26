"""Request dependencies for the detachable, multi-tenant Ledger Service.

``get_ledger_db`` resolves the active tenant ``org_id`` and sets Postgres'
transaction-scoped ``app.current_org_id`` GUC for RLS defence-in-depth before
any query runs.

Tenant resolution order:
  1. Explicit ``X-Ledger-Org-Id`` HTTP header (validated UUID; verified active).
  2. ``settings.LEDGER_DEFAULT_ORG_ID`` (SwimBuddz default when header omitted).
  3. Sole-org DB fallback ONLY when the DB has exactly one organization (refuses
     to guess when multiple tenants exist, preventing silent cross-org mixing).

RLS is defence-in-depth: application code MUST still filter every query by
``request.state.org_id``. When ``LEDGER_DATABASE_URL`` is configured (e.g. a
``NOBYPASSRLS`` ``ledger_app`` role or a standalone Postgres instance), the DB
enforces org isolation at the storage engine layer as well.
"""

from __future__ import annotations

import uuid
from typing import AsyncGenerator

from fastapi import Depends, HTTPException, Request, status
from libs.auth.dependencies import get_current_user
from libs.auth.models import AuthUser
from libs.common.config import get_settings
from libs.common.logging import get_logger
from services.ledger_service.app.db import get_ledger_raw_db
from services.ledger_service.models import LedgerUser, Organization
from services.ledger_service.models.enums import (
    LEDGER_ROLE_RANK,
    LedgerRole,
    OrgStatus,
)
from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

ORG_ID_HEADER = "X-Ledger-Org-Id"

# Single-tenant DB fallback (task #24): cache the sole org id so we resolve it
# once when LEDGER_DEFAULT_ORG_ID is unset, rather than querying every request.
_FALLBACK_ORG_ID: uuid.UUID | None = None


def _env_org_id() -> uuid.UUID | None:
    """Parse ``LEDGER_DEFAULT_ORG_ID``. ``None`` if unset; 503 if set but invalid."""
    raw = (get_settings().LEDGER_DEFAULT_ORG_ID or "").strip()
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LEDGER_DEFAULT_ORG_ID is not a valid UUID.",
        ) from exc


def parse_header_org_id(request: Request | None) -> uuid.UUID | None:
    """Parse ``X-Ledger-Org-Id`` header if supplied; 400 on invalid UUID."""
    if request is None:
        return None
    raw = (request.headers.get(ORG_ID_HEADER) or "").strip()
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{ORG_ID_HEADER} header must be a valid UUID.",
        ) from exc


def resolve_org_id(request: Request) -> uuid.UUID:
    """Resolve the organization synchronously (header first, then env var)."""
    header_org = parse_header_org_id(request)
    if header_org is not None:
        return header_org
    org = _env_org_id()
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Ledger organization not configured (LEDGER_DEFAULT_ORG_ID unset).",
        )
    return org


async def _resolve_org_id(
    session: AsyncSession,
    *,
    explicit_org_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """Resolve the tenant org_id: explicit header/arg -> env var -> sole DB org.

    When multiple organizations exist in the DB and neither an explicit org_id
    nor ``LEDGER_DEFAULT_ORG_ID`` is configured, refuses to guess (503).
    """
    if explicit_org_id is not None:
        return explicit_org_id

    env = _env_org_id()
    if env is not None:
        return env

    global _FALLBACK_ORG_ID
    if _FALLBACK_ORG_ID is not None:
        return _FALLBACK_ORG_ID

    ids = (await session.execute(select(Organization.id).limit(2))).scalars().all()
    if len(ids) == 1:
        _FALLBACK_ORG_ID = ids[0]
        logger.warning(
            "LEDGER_DEFAULT_ORG_ID unset; falling back to the sole ledger org %s. "
            "Set the env var or pass %s to make tenant selection explicit.",
            _FALLBACK_ORG_ID,
            ORG_ID_HEADER,
        )
        return _FALLBACK_ORG_ID

    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=(
            "Ledger organization not configured: pass X-Ledger-Org-Id or set "
            f"LEDGER_DEFAULT_ORG_ID (DB has {len(ids)} orgs; auto-resolve requires 1)."
        ),
    )


async def set_org_rls_context(session: AsyncSession, org_id: uuid.UUID) -> None:
    """Bind transaction-scoped ``app.current_org_id`` GUC for Postgres RLS."""
    await session.execute(
        text("SELECT set_config('app.current_org_id', :org, true)"),
        {"org": str(org_id)},
    )


async def get_ledger_db(
    request: Request,
    session: AsyncSession = Depends(get_ledger_raw_db),
) -> AsyncGenerator[AsyncSession, None]:
    """Yield a DB session scoped to the resolved tenant organization.

    When the caller supplies ``X-Ledger-Org-Id``, verifies the organization
    exists and is ``ACTIVE`` before yielding the session.
    """
    header_org_id = parse_header_org_id(request)
    org_id = await _resolve_org_id(session, explicit_org_id=header_org_id)
    await set_org_rls_context(session, org_id)

    if header_org_id is not None:
        org = await session.scalar(
            select(Organization).where(Organization.id == org_id)
        )
        if org is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Ledger organization not found.",
            )
        if org.status != OrgStatus.ACTIVE:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Ledger organization is {org.status.value}.",
            )

    request.state.org_id = org_id
    yield session


def require_ledger_role(minimum: LedgerRole):
    """Dependency factory — require an active LedgerUser with at least `minimum`.

    Matches the authenticated user to a finance-team member in the request's org
    by auth_id or email. Hierarchy: owner ⊇ admin ⊇ accountant ⊇ operations ⊇ viewer.
    A login with no active ledger_users row in the resolved org gets 403 — being
    a platform admin never implies finance access in any org.
    """
    min_rank = LEDGER_ROLE_RANK[minimum]

    async def _dep(
        request: Request,
        user: AuthUser = Depends(get_current_user),
        session: AsyncSession = Depends(get_ledger_db),
    ) -> LedgerUser:
        org_id = request.state.org_id
        match = [LedgerUser.auth_id == user.user_id]
        if user.email:
            match.append(LedgerUser.email == str(user.email))
        ledger_user = (
            (
                await session.execute(
                    select(LedgerUser).where(
                        LedgerUser.org_id == org_id,
                        LedgerUser.deactivated_at.is_(None),
                        or_(*match),
                    )
                )
            )
            .scalars()
            .first()
        )
        if ledger_user is None or LEDGER_ROLE_RANK[ledger_user.role] < min_rank:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient ledger role",
            )
        return ledger_user

    return _dep
