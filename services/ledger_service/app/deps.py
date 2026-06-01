"""Request dependencies for the Ledger Service.

`get_ledger_db` sets the per-request organization context that the RLS policies
read (see migration 298d02a91299). `resolve_org_id` decides which org a request
acts on — Phase 1 (single-tenant SwimBuddz) always resolves to
`settings.LEDGER_DEFAULT_ORG_ID`; the B2B path (org from auth claim / URL) lands
in the productisation phase.

RLS is defence-in-depth: application code must STILL filter every query by
`request.state.org_id`. This dependency is the belt; query-level filtering is
the braces.
"""

from __future__ import annotations

import uuid
from typing import AsyncGenerator

from fastapi import Depends, HTTPException, Request, status
from libs.common.config import get_settings
from libs.db.session import get_async_db
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def resolve_org_id(request: Request) -> uuid.UUID:
    """Resolve the organization this request acts on.

    Phase 1: always SwimBuddz's org from ``LEDGER_DEFAULT_ORG_ID``. Raises 503 if
    the ledger org has not been seeded yet (scripts/seed/ledger_org.py), rather
    than letting an empty value reach the DB (``CAST('' AS uuid)`` would error).
    """
    settings = get_settings()
    raw = (settings.LEDGER_DEFAULT_ORG_ID or "").strip()
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Ledger organization not configured (LEDGER_DEFAULT_ORG_ID unset).",
        )
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LEDGER_DEFAULT_ORG_ID is not a valid UUID.",
        ) from exc


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
    org_id = resolve_org_id(request)
    await session.execute(
        text("SELECT set_config('app.current_org_id', :org, true)"),
        {"org": str(org_id)},
    )
    request.state.org_id = org_id
    yield session
