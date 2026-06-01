"""Manual journal entries + reversals (accountant+).

Manual entries go through the same post_entry path as service emitters (so the
balance/period/idempotency rules are identical). Reversals post an opposite
entry and mark the original reversed — the only sanctioned way to correct a
posted entry. Gateway: /api/v1/admin/finance/journal-entries*.
"""

from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from services.ledger_service.app.deps import get_ledger_db, require_ledger_role
from services.ledger_service.models import LedgerUser
from services.ledger_service.models.enums import LedgerRole
from services.ledger_service.schemas.journal import (
    JournalEntryCreate,
    JournalEntryResult,
    ReverseRequest,
)
from services.ledger_service.services.posting import (
    AlreadyReversedError,
    EntryNotFoundError,
    PeriodClosedError,
    UnbalancedEntryError,
    UnresolvedAccountError,
    post_entry,
    reverse_entry,
)
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/admin/finance", tags=["ledger-admin"])


@router.post(
    "/journal-entries",
    response_model=JournalEntryResult,
    status_code=status.HTTP_201_CREATED,
)
async def create_manual_entry(
    payload: JournalEntryCreate,
    request: Request,
    actor: LedgerUser = Depends(require_ledger_role(LedgerRole.ACCOUNTANT)),
    session: AsyncSession = Depends(get_ledger_db),
) -> JournalEntryResult:
    org_id = request.state.org_id
    try:
        result = await post_entry(
            session,
            org_id=org_id,
            payload=payload,
            posted_by_user_id=actor.id,
            posted_by_service="manual",
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
    "/journal-entries/{entry_id}/reverse",
    response_model=JournalEntryResult,
    status_code=status.HTTP_201_CREATED,
)
async def reverse_journal_entry(
    entry_id: uuid.UUID,
    request: Request,
    actor: LedgerUser = Depends(require_ledger_role(LedgerRole.ACCOUNTANT)),
    session: AsyncSession = Depends(get_ledger_db),
    body: Optional[ReverseRequest] = None,
) -> JournalEntryResult:
    org_id = request.state.org_id
    try:
        result = await reverse_entry(
            session,
            org_id=org_id,
            entry_id=entry_id,
            reversed_by_user_id=actor.id,
            reason=body.reason if body else None,
        )
    except EntryNotFoundError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except (AlreadyReversedError, PeriodClosedError) as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    await session.commit()
    return result
