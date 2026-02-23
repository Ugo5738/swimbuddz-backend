"""Internal service-to-service endpoints for attendance-service.

These endpoints are authenticated with service_role JWT only.
They are NOT exposed through the gateway — only other backend services
call them directly via Docker network.
"""

import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from libs.auth.dependencies import require_service_role
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from pydantic import BaseModel
from services.attendance_service.models import AttendanceRecord
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/internal", tags=["internal"])


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class AttendanceRecordBasic(BaseModel):
    id: str
    session_id: str
    member_id: str
    status: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/attendance/member/{member_id}",
    response_model=List[AttendanceRecordBasic],
)
async def get_member_attendance(
    member_id: uuid.UUID,
    session_ids: Optional[str] = Query(
        None, description="Comma-separated session IDs to filter by"
    ),
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Get attendance records for a member, optionally filtered by session IDs."""
    query = select(AttendanceRecord).where(
        AttendanceRecord.member_id == member_id,
    )
    if session_ids:
        ids = [uuid.UUID(sid.strip()) for sid in session_ids.split(",") if sid.strip()]
        query = query.where(AttendanceRecord.session_id.in_(ids))

    result = await db.execute(query)
    records = result.scalars().all()

    return [
        AttendanceRecordBasic(
            id=str(r.id),
            session_id=str(r.session_id),
            member_id=str(r.member_id),
            status=r.status.value if hasattr(r.status, "value") else str(r.status),
        )
        for r in records
    ]


@router.get(
    "/attendance/session/{session_id}/member-ids",
    response_model=List[str],
)
async def get_session_attendee_member_ids(
    session_id: uuid.UUID,
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Return distinct member ID strings for everyone who has an attendance
    record for the given session.  Used by communications-service to find
    who should receive session-related notifications."""
    query = (
        select(AttendanceRecord.member_id)
        .where(AttendanceRecord.session_id == session_id)
        .distinct()
    )
    result = await db.execute(query)
    return [str(mid) for mid in result.scalars().all()]
