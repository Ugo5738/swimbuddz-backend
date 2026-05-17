"""Admin: volunteer-role CRUD."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from services.volunteer_service.models import VolunteerRole
from services.volunteer_service.schemas import (
    VolunteerRoleCreate,
    VolunteerRoleResponse,
    VolunteerRoleUpdate,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()


@router.post("/roles", response_model=VolunteerRoleResponse, status_code=201)
async def create_role(
    data: VolunteerRoleCreate,
    admin: Annotated[AuthUser, Depends(require_admin)],
    db: AsyncSession = Depends(get_async_db),
):
    role = VolunteerRole(**data.model_dump())
    db.add(role)
    await db.commit()
    await db.refresh(role)
    result = {c.key: getattr(role, c.key) for c in role.__table__.columns}
    result["active_volunteers_count"] = 0
    return result


@router.patch("/roles/{role_id}", response_model=VolunteerRoleResponse)
async def update_role(
    role_id: uuid.UUID,
    data: VolunteerRoleUpdate,
    admin: Annotated[AuthUser, Depends(require_admin)],
    db: AsyncSession = Depends(get_async_db),
):
    role = (
        await db.execute(select(VolunteerRole).where(VolunteerRole.id == role_id))
    ).scalar_one_or_none()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(role, field, value)
    await db.commit()
    await db.refresh(role)
    result = {c.key: getattr(role, c.key) for c in role.__table__.columns}
    result["active_volunteers_count"] = 0
    return result


@router.delete("/roles/{role_id}", status_code=204)
async def deactivate_role(
    role_id: uuid.UUID,
    admin: Annotated[AuthUser, Depends(require_admin)],
    db: AsyncSession = Depends(get_async_db),
):
    role = (
        await db.execute(select(VolunteerRole).where(VolunteerRole.id == role_id))
    ).scalar_one_or_none()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    role.is_active = False
    await db.commit()
