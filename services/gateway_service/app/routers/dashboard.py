from typing import List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from libs.auth.dependencies import get_current_user, require_admin
from libs.auth.models import AuthUser
from libs.common.logging import get_logger
from pydantic import BaseModel
from services.attendance_service.schemas import AttendanceResponse
from services.communications_service.schemas import AnnouncementResponse
from services.gateway_service.app import clients

# Import schemas for reuse (these are just Pydantic models, safe to import)
from services.members_service.schemas import MemberResponse
from services.sessions_service.schemas import SessionResponse

router = APIRouter(tags=["dashboard"])
logger = get_logger(__name__)


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


def _extract_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text or "Unknown error"

    if isinstance(payload, dict) and "detail" in payload:
        return str(payload["detail"])
    return str(payload)


async def _fetch_json(
    client: clients.ServiceClient,
    path: str,
    label: str,
    headers: Optional[dict[str, str]] = None,
):
    try:
        response = await client.get(path, headers=headers)
        return response.json()
    except httpx.HTTPStatusError as exc:
        detail = _extract_detail(exc.response)
        logger.warning(
            "Dashboard service error",
            extra={
                "extra_fields": {
                    "service": label,
                    "status_code": exc.response.status_code,
                    "detail": detail,
                }
            },
        )
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=f"{label} service error: {detail}",
        )
    except httpx.RequestError as exc:
        logger.error(
            "Dashboard service unavailable",
            extra={"extra_fields": {"service": label, "error": str(exc)}},
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"{label} service unavailable",
        )


@router.get("/me/dashboard", response_model=MemberDashboardResponse)
async def get_member_dashboard(
    request: Request,
    current_user: AuthUser = Depends(get_current_user),
):
    """
    Get the dashboard for the current member.
    Aggregates profile, upcoming sessions, recent attendance, and announcements.
    """
    # 1. Get Member Profile
    auth_header = request.headers.get("Authorization")
    member_headers = {"Authorization": auth_header} if auth_header else None

    try:
        member = await _fetch_json(
            clients.members_client, "/members/me", "Members", member_headers
        )
    except HTTPException as exc:
        if exc.status_code == status.HTTP_404_NOT_FOUND:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Member profile not found. Please complete registration.",
            ) from exc
        raise

    # 2. Get Upcoming Sessions (next 5)
    # TODO: Add limit/filter params to sessions service
    sessions = await _fetch_json(clients.sessions_client, "/sessions/", "Sessions")
    upcoming_sessions = sessions[:5]  # Mock limit for now

    # 3. Get Recent Attendance (last 5)
    # TODO: Add endpoint to attendance service
    recent_attendance = []

    # 4. Get Latest Announcements (last 3)
    announcements = await _fetch_json(
        clients.communications_client, "/announcements/", "Communications"
    )
    latest_announcements = announcements[:3]

    return MemberDashboardResponse(
        member=member,
        upcoming_sessions=upcoming_sessions,
        recent_attendance=recent_attendance,
        latest_announcements=latest_announcements,
    )


@router.get("/admin/dashboard-stats", response_model=AdminDashboardStats)
async def get_admin_dashboard_stats(
    request: Request,
    current_user: AuthUser = Depends(require_admin),
):
    """
    Get statistics for the admin dashboard.
    """
    # 1. Member Stats
    auth_header = request.headers.get("Authorization")
    service_headers = {"Authorization": auth_header} if auth_header else None

    member_stats = await _fetch_json(
        clients.members_client, "/members/stats", "Members", service_headers
    )

    # 2. Session Stats
    session_stats = await _fetch_json(
        clients.sessions_client, "/sessions/stats", "Sessions", service_headers
    )

    # 3. Announcement Stats
    announcement_stats = await _fetch_json(
        clients.communications_client,
        "/announcements/stats",
        "Communications",
        service_headers,
    )

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
