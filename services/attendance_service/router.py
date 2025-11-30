import uuid
from typing import List
import httpx

from fastapi import APIRouter, Depends, HTTPException, status, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import get_current_user, require_admin
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from libs.common.config import get_settings
from services.attendance_service.models import AttendanceRecord
from services.attendance_service.schemas import AttendanceResponse, AttendanceCreate, PublicAttendanceCreate
from services.members_service.models import Member
from services.sessions_service.models import Session

router = APIRouter(tags=["attendance"])
settings = get_settings()


async def get_current_member(
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> Member:
    query = select(Member).where(Member.auth_id == current_user.user_id)
    result = await db.execute(query)
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member profile not found. Please complete registration.",
        )
    return member


@router.post("/sessions/{session_id}/sign-in", response_model=AttendanceResponse)
async def sign_in_to_session(
    session_id: uuid.UUID,
    attendance_in: AttendanceCreate,
    current_member: Member = Depends(get_current_member),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Sign in to a session. Idempotent upsert.
    """
    # Verify session exists
    query = select(Session).where(Session.id == session_id)
    result = await db.execute(query)
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Check for existing attendance
    query = select(AttendanceRecord).where(
        AttendanceRecord.session_id == session_id,
        AttendanceRecord.member_id == current_member.id
    )
    result = await db.execute(query)
    attendance = result.scalar_one_or_none()

    if attendance:
        # Update existing
        attendance.status = attendance_in.status
        attendance.role = attendance_in.role
        attendance.notes = attendance_in.notes
    else:
        # Create new
        attendance = AttendanceRecord(
            session_id=session_id,
            member_id=current_member.id,
            status=attendance_in.status,
            role=attendance_in.role,
            notes=attendance_in.notes,
        )
        db.add(attendance)

    await db.commit()
    await db.refresh(attendance)
    await _send_ride_preference(session_id, current_member.id, attendance_in)
    return attendance


@router.post("/sessions/{session_id}/attendance/public", response_model=AttendanceResponse)
async def public_sign_in_to_session(
    session_id: uuid.UUID,
    attendance_in: PublicAttendanceCreate,
    db: AsyncSession = Depends(get_async_db),
):
    """
    Public sign in to a session (no auth required). Idempotent upsert.
    """
    # Verify session exists
    query = select(Session).where(Session.id == session_id)
    result = await db.execute(query)
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Verify member exists
    query = select(Member).where(Member.id == attendance_in.member_id)
    result = await db.execute(query)
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    # Check for existing attendance
    query = select(AttendanceRecord).where(
        AttendanceRecord.session_id == session_id,
        AttendanceRecord.member_id == attendance_in.member_id
    )
    result = await db.execute(query)
    attendance = result.scalar_one_or_none()

    if attendance:
        # Update existing
        attendance.status = attendance_in.status
        attendance.role = attendance_in.role
        attendance.notes = attendance_in.notes
    else:
        # Create new
        attendance = AttendanceRecord(
            session_id=session_id,
            member_id=attendance_in.member_id,
            status=attendance_in.status,
            role=attendance_in.role,
            notes=attendance_in.notes,
        )
        db.add(attendance)

    await db.commit()
    await db.refresh(attendance)
    await _send_ride_preference(session_id, attendance_in.member_id, attendance_in)
    return attendance
@router.get("/sessions/{session_id}/attendance", response_model=List[AttendanceResponse])
async def list_session_attendance(
    session_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    List all attendees for a session (Admin only).
    """
    query = select(AttendanceRecord, Member).join(Member).where(AttendanceRecord.session_id == session_id)
    result = await db.execute(query)
    rows = result.all()
    
    responses = []
    for attendance, member in rows:
        # Convert SQLAlchemy model to Pydantic model
        resp = AttendanceResponse.model_validate(attendance)
        # Manually populate extra fields
        resp.member_name = f"{member.first_name} {member.last_name}"
        resp.member_email = member.email
        responses.append(resp)
        
    return responses


@router.get("/me/attendance", response_model=List[AttendanceResponse])
async def get_my_attendance_history(
    current_member: Member = Depends(get_current_member),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Get attendance history for the current member.
    """
    query = select(AttendanceRecord).where(
        AttendanceRecord.member_id == current_member.id
    ).order_by(AttendanceRecord.created_at.desc())
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/sessions/{session_id}/pool-list")
async def get_pool_list_csv(
    session_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Export pool list as CSV (Admin only).
    """
    # Join with Member to get names
    query = select(AttendanceRecord, Member).join(Member).where(AttendanceRecord.session_id == session_id)
    result = await db.execute(query)
    rows = result.all()

    # Simple CSV generation
    csv_content = "First Name,Last Name,Email,Status,Role,Notes\n"
    for attendance, member in rows:
        csv_content += f"{member.first_name},{member.last_name},{member.email},{attendance.status},{attendance.role},{attendance.notes or ''}\n"

    return Response(content=csv_content, media_type="text/csv")


async def _send_ride_preference(session_id: uuid.UUID, member_id: uuid.UUID, payload: AttendanceCreate):
    """
    Fire-and-forget call to transport service to upsert ride preferences.
    """
    try:
        json_payload = {
            "member_id": str(member_id),
            "ride_share_option": payload.ride_share_option,
            "needs_ride": payload.needs_ride,
            "can_offer_ride": payload.can_offer_ride,
            "ride_notes": payload.notes,
            "pickup_location": payload.pickup_location,
        }
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                f"{settings.TRANSPORT_SERVICE_URL}/transport/sessions/{session_id}/rides",
                json=json_payload,
            )
    except Exception:
        # Do not fail attendance if transport call fails; TODO: add logging
        pass
