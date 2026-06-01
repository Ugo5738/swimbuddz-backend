"""Finance-team user management (P1.6b).

Admin/owner-gated CRUD over ledger_users so finance staff can be onboarded
without DB access. No privilege escalation: you can't grant, modify, or
deactivate a role higher than your own. Every change is audit-logged.

Gateway: /api/v1/admin/finance/users/* -> /admin/finance/users/*.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from libs.common.datetime_utils import utc_now
from services.ledger_service.app.deps import get_ledger_db, require_ledger_role
from services.ledger_service.models import AuditLog, LedgerUser
from services.ledger_service.models.enums import (
    LEDGER_ROLE_RANK,
    AuditActionType,
    LedgerRole,
)
from services.ledger_service.schemas.users import (
    LedgerUserCreate,
    LedgerUserOut,
    LedgerUserUpdate,
)
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/admin/finance/users", tags=["ledger-admin-users"])


def _guard_rank(actor: LedgerUser, role: LedgerRole) -> None:
    """Block acting on / granting a role higher than the actor's own."""
    if LEDGER_ROLE_RANK[role] > LEDGER_ROLE_RANK[actor.role]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot act on a role higher than your own",
        )


def _audit(session, org_id, actor_id, action, subject_id, payload) -> None:
    session.add(
        AuditLog(
            org_id=org_id,
            actor_user_id=actor_id,
            action=action,
            subject_type="ledger_user",
            subject_id=str(subject_id),
            payload=payload,
        )
    )


@router.get("", response_model=list[LedgerUserOut])
async def list_finance_users(
    request: Request,
    _actor: LedgerUser = Depends(require_ledger_role(LedgerRole.ADMIN)),
    session: AsyncSession = Depends(get_ledger_db),
) -> list[LedgerUser]:
    org_id = request.state.org_id
    rows = (
        (
            await session.execute(
                select(LedgerUser)
                .where(LedgerUser.org_id == org_id)
                .order_by(LedgerUser.created_at)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


@router.post("", response_model=LedgerUserOut, status_code=status.HTTP_201_CREATED)
async def add_finance_user(
    payload: LedgerUserCreate,
    request: Request,
    actor: LedgerUser = Depends(require_ledger_role(LedgerRole.ADMIN)),
    session: AsyncSession = Depends(get_ledger_db),
) -> LedgerUser:
    org_id = request.state.org_id
    _guard_rank(actor, payload.role)

    match = []
    if payload.auth_id:
        match.append(LedgerUser.auth_id == payload.auth_id)
    if payload.email:
        match.append(LedgerUser.email == str(payload.email))
    existing = (
        (
            await session.execute(
                select(LedgerUser).where(LedgerUser.org_id == org_id, or_(*match))
            )
        )
        .scalars()
        .first()
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A finance user with that email/auth_id already exists",
        )

    ledger_user = LedgerUser(
        org_id=org_id,
        role=payload.role,
        email=str(payload.email) if payload.email else None,
        auth_id=payload.auth_id,
    )
    session.add(ledger_user)
    await session.flush()
    _audit(
        session,
        org_id,
        actor.id,
        AuditActionType.USER_ADDED,
        ledger_user.id,
        {"role": payload.role.value, "email": ledger_user.email},
    )
    await session.commit()
    await session.refresh(ledger_user)
    return ledger_user


@router.patch("/{user_id}", response_model=LedgerUserOut)
async def update_finance_user_role(
    user_id: uuid.UUID,
    payload: LedgerUserUpdate,
    request: Request,
    actor: LedgerUser = Depends(require_ledger_role(LedgerRole.ADMIN)),
    session: AsyncSession = Depends(get_ledger_db),
) -> LedgerUser:
    org_id = request.state.org_id
    ledger_user = await session.get(LedgerUser, user_id)
    if ledger_user is None or ledger_user.org_id != org_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Finance user not found"
        )
    _guard_rank(actor, ledger_user.role)  # can't modify a higher-ranked user
    _guard_rank(actor, payload.role)  # can't promote above your own rank
    old = ledger_user.role
    ledger_user.role = payload.role
    _audit(
        session,
        org_id,
        actor.id,
        AuditActionType.USER_ROLE_CHANGED,
        ledger_user.id,
        {"from": old.value, "to": payload.role.value},
    )
    await session.commit()
    await session.refresh(ledger_user)
    return ledger_user


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_finance_user(
    user_id: uuid.UUID,
    request: Request,
    actor: LedgerUser = Depends(require_ledger_role(LedgerRole.ADMIN)),
    session: AsyncSession = Depends(get_ledger_db),
):
    org_id = request.state.org_id
    ledger_user = await session.get(LedgerUser, user_id)
    if ledger_user is None or ledger_user.org_id != org_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Finance user not found"
        )
    if ledger_user.id == actor.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot deactivate yourself",
        )
    _guard_rank(actor, ledger_user.role)
    if ledger_user.deactivated_at is None:
        ledger_user.deactivated_at = utc_now()
        _audit(
            session,
            org_id,
            actor.id,
            AuditActionType.USER_DEACTIVATED,
            ledger_user.id,
            {"email": ledger_user.email},
        )
        await session.commit()
    return None
