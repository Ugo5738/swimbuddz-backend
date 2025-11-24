from typing import List
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from libs.auth.dependencies import get_current_user, require_admin
from libs.auth.models import AuthUser
from services.gateway_service.app import clients

# Import schemas for reuse (these are just Pydantic models, safe to import)
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
):
    """
    Get the dashboard for the current member.
    Aggregates profile, upcoming sessions, recent attendance, and announcements.
    """
    # 1. Get Member Profile
    try:
        member = await clients.members_client.get("/members/me", headers={"Authorization": f"Bearer {current_user.token}"})
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member profile not found. Please complete registration.",
        )

    # 2. Get Upcoming Sessions (next 5)
    # TODO: Add limit/filter params to sessions service
    sessions = await clients.sessions_client.get("/sessions/")
    upcoming_sessions = sessions[:5] # Mock limit for now

    # 3. Get Recent Attendance (last 5)
    # TODO: Add endpoint to attendance service
    recent_attendance = [] 

    # 4. Get Latest Announcements (last 3)
    announcements = await clients.communications_client.get("/announcements/")
    latest_announcements = announcements[:3]

    return MemberDashboardResponse(
        member=member,
        upcoming_sessions=upcoming_sessions,
        recent_attendance=recent_attendance,
        latest_announcements=latest_announcements,
    )


@router.get("/admin/dashboard-stats", response_model=AdminDashboardStats)
async def get_admin_dashboard_stats(
    current_user: AuthUser = Depends(require_admin),
):
    """
    Get statistics for the admin dashboard.
    """
    # 1. Member Stats
    member_stats = await clients.members_client.get("/members/stats")
    
    # 2. Session Stats
    session_stats = await clients.sessions_client.get("/sessions/stats")

    # 3. Announcement Stats
    announcement_stats = await clients.communications_client.get("/announcements/stats")

    return AdminDashboardStats(
        total_members=member_stats.get("total_members", 0),
        active_members=member_stats.get("active_members", 0),
        upcoming_sessions_count=session_stats.get("upcoming_sessions_count", 0),
        recent_announcements_count=announcement_stats.get("recent_announcements_count", 0),
    )
