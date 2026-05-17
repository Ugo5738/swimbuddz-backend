"""Admin-only attendance endpoints (pool-list CSV export, bulk delete)."""

import uuid

from fastapi import APIRouter, Depends, Response
from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.common.service_client import get_members_bulk
from libs.db.session import get_async_db
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.attendance_service.models import AttendanceRecord

router = APIRouter()


@router.get("/sessions/{session_id}/pool-list")
async def get_pool_list_csv(
    session_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Export pool list as CSV (Admin only).
    """
    # Get attendance records
    query = select(AttendanceRecord).where(AttendanceRecord.session_id == session_id)
    result = await db.execute(query)
    records = result.scalars().all()

    # Bulk-lookup member details
    pool_member_ids = list({str(r.member_id) for r in records})
    pool_members = await get_members_bulk(pool_member_ids, calling_service="attendance")
    pool_members_map = {m["id"]: m for m in pool_members}

    # Simple CSV generation
    csv_content = "First Name,Last Name,Email,Notes\n"
    for attendance in records:
        member = pool_members_map.get(str(attendance.member_id), {})
        csv_content += f"{member.get('first_name', '')},{member.get('last_name', '')},{member.get('email', '')},{attendance.notes or ''}\n"

    return Response(content=csv_content, media_type="text/csv")


@router.delete("/admin/members/{member_id}")
async def admin_delete_member_attendance(
    member_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Delete attendance records for a member (Admin only).
    """
    result = await db.execute(
        delete(AttendanceRecord).where(AttendanceRecord.member_id == member_id)
    )
    await db.commit()
    return {"deleted": result.rowcount or 0}
