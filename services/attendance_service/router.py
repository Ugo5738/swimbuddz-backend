import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import get_current_user, require_admin
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from services.attendance_service.models import SessionAttendance, PaymentStatus
from services.attendance_service.schemas import AttendanceResponse, AttendanceCreate
from services.members_service.models import Member
from services.sessions_service.models import Session

router = APIRouter(tags=["attendance"])


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
    query = select(SessionAttendance).where(
        SessionAttendance.session_id == session_id,
        SessionAttendance.member_id == current_member.id
    )
    result = await db.execute(query)
    attendance = result.scalar_one_or_none()

    if attendance:
        # Update existing
        attendance.needs_ride = attendance_in.needs_ride
        attendance.can_offer_ride = attendance_in.can_offer_ride
        attendance.ride_notes = attendance_in.ride_notes
    else:
        # Create new
        attendance = SessionAttendance(
            session_id=session_id,
            member_id=current_member.id,
            needs_ride=attendance_in.needs_ride,
            can_offer_ride=attendance_in.can_offer_ride,
            ride_notes=attendance_in.ride_notes,
            payment_status=PaymentStatus.PENDING,
            total_fee=session.pool_fee
        )
        db.add(attendance)

    await db.commit()
    await db.refresh(attendance)
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
    query = select(SessionAttendance).where(SessionAttendance.session_id == session_id)
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/me/attendance", response_model=List[AttendanceResponse])
async def get_my_attendance_history(
    current_member: Member = Depends(get_current_member),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Get attendance history for the current member.
    """
    query = select(SessionAttendance).where(
        SessionAttendance.member_id == current_member.id
    ).order_by(SessionAttendance.created_at.desc())
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
    query = select(SessionAttendance, Member).join(Member).where(SessionAttendance.session_id == session_id)
    result = await db.execute(query)
    rows = result.all()

    # Simple CSV generation
    csv_content = "First Name,Last Name,Email,Payment Status,Ride Notes\n"
    for attendance, member in rows:
        csv_content += f"{member.first_name},{member.last_name},{member.email},{attendance.payment_status},{attendance.ride_notes or ''}\n"

    return Response(content=csv_content, media_type="text/csv")
