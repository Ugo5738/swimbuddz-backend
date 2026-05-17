"""Admin: manual hours entry."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.common.service_client import get_member_by_auth_id
from libs.db.session import get_async_db
from services.volunteer_service.models import VolunteerHoursLog
from services.volunteer_service.schemas import (
    ManualHoursCreate,
    VolunteerHoursLogResponse,
)
from services.volunteer_service.services import update_profile_aggregates
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()


@router.post("/hours/manual", response_model=VolunteerHoursLogResponse, status_code=201)
async def add_manual_hours(
    data: ManualHoursCreate,
    admin: Annotated[AuthUser, Depends(require_admin)],
    db: AsyncSession = Depends(get_async_db),
):
    _admin = await get_member_by_auth_id(admin.user_id, calling_service="volunteer")
    admin_member_id = uuid.UUID(_admin["id"]) if _admin else None
    log = VolunteerHoursLog(
        member_id=data.member_id,
        hours=data.hours,
        date=data.date,
        role_id=data.role_id,
        source="manual_entry",
        logged_by=admin_member_id,
        notes=data.notes,
    )
    db.add(log)
    await db.commit()

    # Update profile aggregates
    await update_profile_aggregates(db, data.member_id)
    await db.commit()

    await db.refresh(log)
    return log
