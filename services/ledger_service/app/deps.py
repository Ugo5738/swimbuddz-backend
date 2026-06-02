"""Request dependencies for the Ledger Service.

`get_ledger_db` sets the per-request organization context that the RLS policies
read (see migration 298d02a91299). `_resolve_org_id` decides which org a request
acts on — Phase 1 (single-tenant SwimBuddz) resolves to
`settings.LEDGER_DEFAULT_ORG_ID`, falling back to the sole org in the DB if that
env var is unset (task #24 — self-heals the prod 503 we hit). The B2B path (org
from auth claim / URL) lands in the productisation phase.

RLS is defence-in-depth: application code must STILL filter every query by
`request.state.org_id`. This dependency is the belt; query-level filtering is
the braces.

B2B RLS cutover (task #13, pre-B2B infra — NOT done in single-tenant because
there's one org to isolate and the connection is shared by ~18 services). The
org-isolation policies are already defined on every org-keyed ledger table
(298d02a91299 + cf2eae9376e5) but inert under the current `postgres` (BYPASSRLS)
role. To make them enforce at B2B onboarding, in this order:
  1. Create a NOBYPASSRLS role, e.g. `ledger_app`, and GRANT it
     SELECT/INSERT/UPDATE/DELETE on the ledger tables + USAGE on their sequences
     (and on `public` schema). Keep it scoped to ledger tables only.
  2. Add `LEDGER_DATABASE_URL` (that role) to `.env.prod` and route ONLY the
     ledger service's engine to it — do not repoint the shared `DATABASE_URL`.
  3. Verify with a cross-org isolation test under the new role (set
     app.current_org_id to org A, confirm org B's rows are invisible) before and
     after cutover; the coverage test in test_ledger_rls_and_org.py guards that
     the policies exist.
"""

from __future__ import annotations

import uuid
from typing import AsyncGenerator

from fastapi import Depends, HTTPException, Request, status
from libs.auth.dependencies import get_current_user
from libs.auth.models import AuthUser
from libs.common.config import get_settings
from libs.common.logging import get_logger
from libs.db.session import get_async_db
from services.ledger_service.models import LedgerUser, Organization
from services.ledger_service.models.enums import LEDGER_ROLE_RANK, LedgerRole
from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

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


def resolve_org_id(request: Request) -> uuid.UUID:
    """Resolve the organization from the env var only (sync).

    Raises 503 if unset. Request handling goes through the DB-fallback-aware
    :func:`_resolve_org_id` in ``get_ledger_db``; this stays for any sync caller.
    """
    org = _env_org_id()
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Ledger organization not configured (LEDGER_DEFAULT_ORG_ID unset).",
        )
    return org


async def _resolve_org_id(session: AsyncSession) -> uuid.UUID:
    """Resolve the org for a request: env var first, else the sole org in the DB.

    Hardening (task #24): if ``LEDGER_DEFAULT_ORG_ID`` is unset/lost, a single-
    tenant deployment self-heals by using the only org rather than 503-ing the
    whole finance surface (the exact prod incident this guards against). Refuses
    to guess when the DB holds zero or many orgs — that genuinely needs the env
    var. The resolved id is cached process-wide; env always takes precedence, so
    setting the var later overrides the fallback without a restart-order trap.
    """
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
            "Set the env var to make this explicit.",
            _FALLBACK_ORG_ID,
        )
        return _FALLBACK_ORG_ID

    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=(
            "Ledger organization not configured: LEDGER_DEFAULT_ORG_ID unset and "
            f"the DB has {len(ids)} orgs (need exactly 1 to auto-resolve)."
        ),
    )


async def get_ledger_db(
    request: Request,
    session: AsyncSession = Depends(get_async_db),
) -> AsyncGenerator[AsyncSession, None]:
    """Yield a DB session with the org RLS context set for this request.

    Sets ``app.current_org_id`` transaction-scoped via ``set_config(..., true)``
    (the function form — ``SET LOCAL x = :v`` cannot bind a parameter). This must
    be the session's first statement so it shares the transaction with the
    request's subsequent queries; the GUC is reset on COMMIT, which is correct
    for the single-transaction-per-request shape used here.
    """
    org_id = await _resolve_org_id(session)
    await session.execute(
        text("SELECT set_config('app.current_org_id', :org, true)"),
        {"org": str(org_id)},
    )
    request.state.org_id = org_id
    yield session


def require_ledger_role(minimum: LedgerRole):
    """Dependency factory — require an active LedgerUser with at least `minimum`.

    Matches the authenticated user to a finance-team member in the request's org
    by auth_id or email. Hierarchy: owner ⊇ admin ⊇ accountant ⊇ viewer. A login
    with no active ledger_users row gets 403 — being a SwimBuddz admin does not
    imply finance access. Service-role emitters use require_service_role and
    never hit this.
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
