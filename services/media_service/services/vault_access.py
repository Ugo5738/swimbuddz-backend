"""Authorization helpers for the private media vault."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import is_admin_or_service
from libs.auth.models import AuthUser
from libs.common.datetime_utils import utc_now
from libs.common.service_client import get_member_by_auth_id
from services.media_service.models import MediaVault, MediaVaultGrant

ROLE_RANK = {"contributor": 1, "curator": 2, "admin": 3}


@dataclass(frozen=True)
class VaultActor:
    auth_id: uuid.UUID
    member_id: Optional[uuid.UUID]
    is_admin: bool


async def resolve_actor(user: AuthUser) -> VaultActor:
    try:
        auth_id = uuid.UUID(str(user.user_id))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authenticated user identifier",
        ) from exc
    admin = is_admin_or_service(user)
    member = await get_member_by_auth_id(str(user.user_id), calling_service="media")
    member_id = uuid.UUID(str(member["id"])) if member else None
    return VaultActor(auth_id=auth_id, member_id=member_id, is_admin=admin)


async def effective_vault_role(
    db: AsyncSession,
    *,
    vault_id: uuid.UUID,
    actor: VaultActor,
    at: Optional[datetime] = None,
) -> Optional[str]:
    if actor.is_admin:
        return "admin"
    if not actor.member_id:
        return None
    now = at or utc_now()
    rows = await db.execute(
        select(MediaVaultGrant.role).where(
            MediaVaultGrant.vault_id == vault_id,
            MediaVaultGrant.member_id == actor.member_id,
            MediaVaultGrant.starts_at <= now,
            MediaVaultGrant.expires_at >= now,
            MediaVaultGrant.revoked_at.is_(None),
        )
    )
    roles = list(rows.scalars().all())
    return max(roles, key=lambda role: ROLE_RANK.get(role, 0)) if roles else None


async def require_vault_role(
    db: AsyncSession,
    *,
    vault: MediaVault,
    actor: VaultActor,
    minimum: str,
) -> str:
    role = await effective_vault_role(db, vault_id=vault.id, actor=actor)
    if not role or ROLE_RANK.get(role, 0) < ROLE_RANK[minimum]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"{minimum.capitalize()} vault access is required",
        )
    return role


async def require_upload_window(vault: MediaVault) -> None:
    now = utc_now()
    if vault.status == "archived":
        raise HTTPException(status_code=409, detail="This vault is archived")
    if now < vault.upload_opens_at:
        raise HTTPException(status_code=409, detail="The upload window is not open yet")
    if now > vault.upload_closes_at:
        raise HTTPException(status_code=409, detail="The upload window has closed")


async def get_vault_or_404(db: AsyncSession, vault_id: uuid.UUID) -> MediaVault:
    result = await db.execute(select(MediaVault).where(MediaVault.id == vault_id))
    vault = result.scalar_one_or_none()
    if not vault:
        raise HTTPException(status_code=404, detail="Media vault not found")
    return vault
