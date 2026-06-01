"""Internal service-to-service routes for the Ledger Service.

Emitters (payments_service, wallet_service, …) post journal entries here using a
service-role JWT (validated via libs.auth.require_service_role). Org context is
resolved by get_ledger_db (Phase 1: LEDGER_DEFAULT_ORG_ID). The route owns the
transaction boundary — it commits on success, rolls back on error.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from libs.auth.dependencies import require_service_role
from libs.auth.models import AuthUser
from services.ledger_service.app.deps import get_ledger_db
from services.ledger_service.schemas.journal import (
    JournalEntryCreate,
    JournalEntryResult,
)
from services.ledger_service.services.posting import (
    PeriodClosedError,
    UnbalancedEntryError,
    UnresolvedAccountError,
    post_entry,
)
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/internal/ledger", tags=["ledger-internal"])


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
    org_id = request.state.org_id
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
