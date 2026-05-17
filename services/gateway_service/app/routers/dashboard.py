"""Gateway dashboard aggregator.

This router fans out to several domain services and stitches their responses
together. Two service-boundary rules govern the shape of the code here:

  1. **No cross-service schema imports.** The gateway owns its own response
     types; downstream services return JSON which is treated as opaque
     ``dict``. This keeps the gateway deployable independently of any
     internal schema change in members/sessions/attendance/communications.

  2. **Graceful degradation.** A single downstream failure must not blank
     the whole dashboard — instead the affected section returns empty data
     plus an ``errors`` marker so the frontend can render a partial UI.
"""

from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from libs.auth.dependencies import get_current_user, require_admin
from libs.auth.models import AuthUser
from libs.common.logging import get_logger
from pydantic import BaseModel

from services.gateway_service.app import clients

router = APIRouter(tags=["dashboard"])
logger = get_logger(__name__)


# Gateway-owned response shapes. The body fields are deliberately typed as
# generic dicts: the gateway is a pass-through and must not couple to the
# Pydantic schemas of downstream services.


class MemberDashboardResponse(BaseModel):
    member: Optional[Dict[str, Any]] = None
    upcoming_sessions: List[Dict[str, Any]] = []
    recent_attendance: List[Dict[str, Any]] = []
    latest_announcements: List[Dict[str, Any]] = []
    # Per-section error markers, populated when a downstream service is
    # unavailable. Keys are section names (e.g. "sessions", "announcements").
    errors: Dict[str, str] = {}


class AdminDashboardStats(BaseModel):
    total_members: int = 0
    active_members: int = 0
    approved_members: int = 0
    pending_approvals: int = 0
    upcoming_sessions_count: int = 0
    recent_announcements_count: int = 0
    errors: Dict[str, str] = {}


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
    """Fetch JSON from a downstream service, raising HTTPException on failure.

    Use this when the failure should propagate (e.g. the member-profile
    fetch — without a profile the whole dashboard is meaningless). For
    side sections that should degrade gracefully, use ``_fetch_optional``.
    """
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


async def _fetch_optional(
    client: clients.ServiceClient,
    path: str,
    label: str,
    headers: Optional[dict[str, str]] = None,
):
    """Fetch JSON from a downstream service; on failure return ``(None, msg)``.

    Used for non-critical dashboard sections so that one bad service doesn't
    blank the whole page. Caller decides what to substitute (typically an
    empty list) and records the error message under ``errors[label]``.
    """
    try:
        response = await client.get(path, headers=headers)
        return response.json(), None
    except httpx.HTTPStatusError as exc:
        detail = _extract_detail(exc.response)
        logger.warning(
            "Dashboard service error (degraded)",
            extra={
                "extra_fields": {
                    "service": label,
                    "status_code": exc.response.status_code,
                    "detail": detail,
                }
            },
        )
        return None, f"{label} service error ({exc.response.status_code})"
    except httpx.RequestError as exc:
        logger.error(
            "Dashboard service unavailable (degraded)",
            extra={"extra_fields": {"service": label, "error": str(exc)}},
        )
        return None, f"{label} service unavailable"


@router.get("/me/dashboard", response_model=MemberDashboardResponse)
async def get_member_dashboard(
    request: Request,
    current_user: AuthUser = Depends(get_current_user),
):
    """Get the dashboard for the current member.

    Aggregates profile, upcoming sessions, recent attendance, and
    announcements. The member-profile fetch is required; the side sections
    degrade gracefully.
    """
    auth_header = request.headers.get("Authorization")
    member_headers = {"Authorization": auth_header} if auth_header else None

    # 1. Member profile — required; failure propagates.
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

    errors: Dict[str, str] = {}

    # 2. Upcoming sessions — graceful.
    # TODO: Add limit/filter params to sessions service
    sessions_data, sessions_err = await _fetch_optional(
        clients.sessions_client, "/sessions/", "Sessions"
    )
    if sessions_err:
        errors["sessions"] = sessions_err
    upcoming_sessions = (sessions_data or [])[:5]

    # 3. Recent attendance — graceful.
    # TODO: Add endpoint to attendance service
    recent_attendance: List[Dict[str, Any]] = []

    # 4. Latest announcements — graceful.
    announcements_data, announcements_err = await _fetch_optional(
        clients.communications_client,
        "/announcements/",
        "Communications",
        member_headers,
    )
    if announcements_err:
        errors["announcements"] = announcements_err
    latest_announcements = (announcements_data or [])[:3]

    return MemberDashboardResponse(
        member=member,
        upcoming_sessions=upcoming_sessions,
        recent_attendance=recent_attendance,
        latest_announcements=latest_announcements,
        errors=errors,
    )


@router.get("/admin/dashboard-stats", response_model=AdminDashboardStats)
async def get_admin_dashboard_stats(
    request: Request,
    current_user: AuthUser = Depends(require_admin),
):
    """Get statistics for the admin dashboard.

    All three downstream fetches degrade gracefully — admins should see
    whatever stats are available rather than a hard 5xx when one service
    is down.
    """
    auth_header = request.headers.get("Authorization")
    service_headers = {"Authorization": auth_header} if auth_header else None

    errors: Dict[str, str] = {}

    member_stats, err = await _fetch_optional(
        clients.members_client, "/members/stats", "Members", service_headers
    )
    if err:
        errors["members"] = err
        member_stats = {}

    session_stats, err = await _fetch_optional(
        clients.sessions_client, "/sessions/stats", "Sessions", service_headers
    )
    if err:
        errors["sessions"] = err
        session_stats = {}

    announcement_stats, err = await _fetch_optional(
        clients.communications_client,
        "/announcements/stats",
        "Communications",
        service_headers,
    )
    if err:
        errors["announcements"] = err
        announcement_stats = {}

    return AdminDashboardStats(
        total_members=member_stats.get("total_members", 0),
        active_members=member_stats.get("active_members", 0),
        approved_members=member_stats.get("approved_members", 0),
        pending_approvals=member_stats.get("pending_approvals", 0),
        upcoming_sessions_count=session_stats.get("upcoming_sessions_count", 0),
        recent_announcements_count=announcement_stats.get(
            "recent_announcements_count", 0
        ),
        errors=errors,
    )
