from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from libs.auth.dependencies import get_current_user, require_admin
from libs.auth.models import AuthUser
from pydantic import BaseModel
from services.attendance_service.schemas import AttendanceResponse
from services.communications_service.schemas import AnnouncementResponse
from services.gateway_service.app import clients

# Import schemas for reuse (these are just Pydantic models, safe to import)
from services.members_service.schemas import MemberResponse
from services.sessions_service.schemas import SessionResponse

router = APIRouter(tags=["dashboard"])


class MemberDashboardResponse(BaseModel):
    member: MemberResponse
    upcoming_sessions: List[SessionResponse]
    recent_attendance: List[AttendanceResponse]
    latest_announcements: List[AnnouncementResponse]


class AdminDashboardStats(BaseModel):
    total_members: int
    active_members: int
    approved_members: int
    pending_approvals: int
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
        member_response = await clients.members_client.get(
            "/members/me", headers={"Authorization": f"Bearer {current_user.token}"}
        )
        member = member_response.json()
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member profile not found. Please complete registration.",
        )

    # 2. Get Upcoming Sessions (next 5)
    # TODO: Add limit/filter params to sessions service
    sessions_response = await clients.sessions_client.get("/sessions/")
    sessions = sessions_response.json()
    upcoming_sessions = sessions[:5]  # Mock limit for now

    # 3. Get Recent Attendance (last 5)
    # TODO: Add endpoint to attendance service
    recent_attendance = []

    # 4. Get Latest Announcements (last 3)
    announcements_response = await clients.communications_client.get("/announcements/")
    announcements = announcements_response.json()
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
    member_stats_response = await clients.members_client.get("/members/stats")
    member_stats = member_stats_response.json()

    # 2. Session Stats
    session_stats_response = await clients.sessions_client.get("/sessions/stats")
    session_stats = session_stats_response.json()

    # 3. Announcement Stats
    announcement_stats_response = await clients.communications_client.get(
        "/announcements/stats"
    )
    announcement_stats = announcement_stats_response.json()

    return AdminDashboardStats(
        total_members=member_stats.get("total_members", 0),
        active_members=member_stats.get("active_members", 0),
        approved_members=member_stats.get("approved_members", 0),
        pending_approvals=member_stats.get("pending_approvals", 0),
        upcoming_sessions_count=session_stats.get("upcoming_sessions_count", 0),
        recent_announcements_count=announcement_stats.get(
            "recent_announcements_count", 0
        ),
    )
