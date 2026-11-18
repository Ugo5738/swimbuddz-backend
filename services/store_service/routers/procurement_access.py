"""Dedicated procurement grants. A JWT role label alone never grants finance access."""
from fastapi import Depends, HTTPException
from libs.auth.dependencies import get_current_user, require_admin
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from services.store_service.models.procurement import StoreProcurementGrant
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def procurement_reader(
    user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> AuthUser:
    # The existing system administrator remains permitted for operational
    # continuity; the separate procurement grant never grants admin authority.
    if user.role == "service_role" or user.has_role("admin"):
        return user
    grant = await db.scalar(select(StoreProcurementGrant).where(
        StoreProcurementGrant.auth_id == user.user_id,
        StoreProcurementGrant.active.is_(True),
    ))
    if grant is None:
        raise HTTPException(status_code=403, detail="Store procurement access required")
    return user


async def procurement_writer(
    user: AuthUser = Depends(procurement_reader),
    db: AsyncSession = Depends(get_async_db),
) -> AuthUser:
    if user.role == "service_role" or user.has_role("admin"):
        return user
    grant = await db.scalar(select(StoreProcurementGrant).where(
        StoreProcurementGrant.auth_id == user.user_id,
        StoreProcurementGrant.role == "procurement_officer",
        StoreProcurementGrant.active.is_(True),
    ))
    if grant is None:
        raise HTTPException(status_code=403, detail="Store procurement write permission required")
    return user
