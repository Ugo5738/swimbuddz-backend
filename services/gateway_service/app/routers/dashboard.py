from typing import List
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import get_current_user, require_admin
from libs.auth.models import AuthUser
from libs.db.session import get_async_db

# Import models from services
from services.members_service.models import Member
from services.sessions_service.models import Session
from services.attendance_service.models import SessionAttendance
from services.communications_service.models import Announcement

# Import schemas for reuse
from services.members_service.schemas import MemberResponse
from services.sessions_service.schemas import SessionResponse
from services.attendance_service.schemas import AttendanceResponse
from services.communications_service.schemas import AnnouncementResponse

router = APIRouter(tags=["dashboard"])


class MemberDashboardResponse(BaseModel):
    member: MemberResponse
    upcoming_sessions: List[SessionResponse]
    recent_attendance: List[AttendanceResponse]
    latest_announcements: List[AnnouncementResponse]


class AdminDashboardStats(BaseModel):
    total_members: int
    active_members: int
    upcoming_sessions_count: int
    recent_announcements_count: int


@router.get("/me/dashboard", response_model=MemberDashboardResponse)
async def get_member_dashboard(
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Get the dashboard for the current member.
    Aggregates profile, upcoming sessions, recent attendance, and announcements.
    """
    # 1. Get Member Profile
    query = select(Member).where(Member.auth_id == current_user.user_id)
    result = await db.execute(query)
    member = result.scalar_one_or_none()

    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member profile not found. Please complete registration.",
        )

    # 2. Get Upcoming Sessions (next 5)
    # Note: In a real app, we might filter by sessions the member is eligible for
    now = datetime.utcnow()
    query = (
        select(Session)
        .where(Session.start_time > now)
        .order_by(Session.start_time.asc())
        .limit(5)
    )
    result = await db.execute(query)
    upcoming_sessions = result.scalars().all()

    # 3. Get Recent Attendance (last 5)
    query = (
        select(SessionAttendance)
        .where(SessionAttendance.member_id == member.id)
        .order_by(SessionAttendance.created_at.desc())
        .limit(5)
    )
    result = await db.execute(query)
    recent_attendance = result.scalars().all()

    # 4. Get Latest Announcements (last 3)
    query = (
        select(Announcement)
        .where(Announcement.published_at <= now)
        .order_by(Announcement.is_pinned.desc(), Announcement.published_at.desc())
        .limit(3)
    )
    result = await db.execute(query)
    latest_announcements = result.scalars().all()

    return MemberDashboardResponse(
        member=member,
        upcoming_sessions=upcoming_sessions,
        recent_attendance=recent_attendance,
        latest_announcements=latest_announcements,
    )


@router.get("/admin/dashboard-stats", response_model=AdminDashboardStats)
async def get_admin_dashboard_stats(
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Get statistics for the admin dashboard.
    """
    # 1. Total Members
    query = select(func.count(Member.id))
    result = await db.execute(query)
    total_members = result.scalar_one() or 0

    # 2. Active Members (assuming registration_complete=True is active for now)
    query = select(func.count(Member.id)).where(Member.registration_complete.is_(True))
    result = await db.execute(query)
    active_members = result.scalar_one() or 0

    # 3. Upcoming Sessions Count
    now = datetime.utcnow()
    query = select(func.count(Session.id)).where(Session.start_time > now)
    result = await db.execute(query)
    upcoming_sessions_count = result.scalar_one() or 0

    # 4. Recent Announcements Count (last 30 days)
    # For simplicity, just total count for now or last 5
    query = select(func.count(Announcement.id))
    result = await db.execute(query)
    recent_announcements_count = result.scalar_one() or 0

    return AdminDashboardStats(
        total_members=total_members,
        active_members=active_members,
        upcoming_sessions_count=upcoming_sessions_count,
        recent_announcements_count=recent_announcements_count,
    )
