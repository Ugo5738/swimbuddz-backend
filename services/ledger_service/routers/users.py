"""Finance-team user management (P1.6b).

Admin/owner-gated CRUD over ledger_users so finance staff can be onboarded
without DB access. No privilege escalation: you can't grant, modify, or
deactivate a role higher than your own. Every change is audit-logged.

Gateway: /api/v1/admin/finance/users/* -> /admin/finance/users/*.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from libs.common.config import get_settings
from libs.common.datetime_utils import utc_now
from libs.common.supabase import invite_user_by_email
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


async def _invite_finance_user(email: str) -> str:
    """Send the Supabase invite for a finance user; return its status string.

    Best-effort — the caller has already committed the ledger_users row, so a
    failed send must not raise. The invite link lands on the password-set page.
    """
    settings = get_settings()
    result = await invite_user_by_email(
        email, redirect_to=f"{settings.FRONTEND_URL}/reset-password"
    )
    return result["status"]


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


@router.get("/me", response_model=LedgerUserOut)
async def get_my_finance_membership(
    actor: LedgerUser = Depends(require_ledger_role(LedgerRole.VIEWER)),
) -> LedgerUser:
    """Return the caller's own finance-team membership (gated at the lowest role).

    403 if the caller has no active ledger_users row. The frontend calls this to
    gate the finance area for finance staff who are NOT global SwimBuddz admins,
    without exposing the rest of /admin. Declared before the /{user_id} routes so
    "me" is never captured as a user id.
    """
    return actor


@router.post("", response_model=LedgerUserOut, status_code=status.HTTP_201_CREATED)
async def add_finance_user(
    payload: LedgerUserCreate,
    request: Request,
    actor: LedgerUser = Depends(require_ledger_role(LedgerRole.ADMIN)),
    session: AsyncSession = Depends(get_ledger_db),
) -> LedgerUserOut:
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

    # Provision the login: email them a Supabase invite to set a password, so an
    # admin never has to add them in the Supabase dashboard. Best-effort — the
    # membership row is already saved; the status tells the caller what happened.
    out = LedgerUserOut.model_validate(ledger_user)
    if ledger_user.email:
        out.invite_status = await _invite_finance_user(ledger_user.email)
    return out


@router.post("/{user_id}/invite", response_model=LedgerUserOut)
async def resend_finance_user_invite(
    user_id: uuid.UUID,
    request: Request,
    actor: LedgerUser = Depends(require_ledger_role(LedgerRole.ADMIN)),
    session: AsyncSession = Depends(get_ledger_db),
) -> LedgerUserOut:
    """Re-send the Supabase invite email for a finance user (admin/owner).

    For when the original invite was lost. Idempotent at Supabase: an already-
    registered user comes back as ``invite_status="exists"`` (they can just log
    in); no second account is created.
    """
    org_id = request.state.org_id
    ledger_user = (
        await session.execute(
            select(LedgerUser).where(
                LedgerUser.org_id == org_id, LedgerUser.id == user_id
            )
        )
    ).scalar_one_or_none()
    if ledger_user is None:
        raise HTTPException(status_code=404, detail="Finance user not found")
    if not ledger_user.email:
        raise HTTPException(
            status_code=422, detail="This finance user has no email to invite."
        )
    out = LedgerUserOut.model_validate(ledger_user)
    out.invite_status = await _invite_finance_user(ledger_user.email)
    return out


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
