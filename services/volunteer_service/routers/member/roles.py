"""Public listing of volunteer roles."""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from libs.db.session import get_async_db
from services.volunteer_service.models import VolunteerProfile, VolunteerRole
from services.volunteer_service.schemas import VolunteerRoleResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()


@router.get("/roles", response_model=list[VolunteerRoleResponse])
async def list_roles(
    active_only: bool = True,
    db: AsyncSession = Depends(get_async_db),
):
    """List volunteer roles (public)."""
    q = select(VolunteerRole).order_by(VolunteerRole.sort_order)
    if active_only:
        q = q.where(VolunteerRole.is_active.is_(True))
    rows = (await db.execute(q)).scalars().all()

    results = []
    for role in rows:
        count = (
            await db.execute(
                select(func.count(VolunteerProfile.id)).where(
                    VolunteerProfile.is_active.is_(True),
                    VolunteerProfile.preferred_roles.any(str(role.id)),
                )
            )
        ).scalar() or 0
        data = {c.key: getattr(role, c.key) for c in role.__table__.columns}
        data["active_volunteers_count"] = count
        results.append(data)
    return results


@router.get("/roles/{role_id}", response_model=VolunteerRoleResponse)
async def get_role(role_id: uuid.UUID, db: AsyncSession = Depends(get_async_db)):
    """Get a single role."""
    role = (
        await db.execute(select(VolunteerRole).where(VolunteerRole.id == role_id))
    ).scalar_one_or_none()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    data = {c.key: getattr(role, c.key) for c in role.__table__.columns}
    data["active_volunteers_count"] = 0
    return data
