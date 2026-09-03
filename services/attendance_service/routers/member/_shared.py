"""Shared dependencies for attendance member-facing routers.

These helpers are imported by `sign_in.py`, `coach_mark.py`, `lists.py`,
and `admin.py`, and are re-exported from `member.py` for tests that import
`get_current_member` directly from
`services.attendance_service.routers.member`.
"""

import uuid

from fastapi import Depends, HTTPException, status
from libs.auth.dependencies import get_current_user, is_admin_or_service
from libs.auth.models import AuthUser
from libs.common.config import get_settings
from libs.common.service_client import (
    check_club_access_batch,
    check_cohort_enrollment,
    get_member_by_auth_id,
    get_member_membership,
    get_pod_by_id,
    get_session_by_id,
    internal_get,
)
from libs.common.datetime_utils import utc_now
from libs.common.session_access import denial_message, evaluate_session_access
from libs.db.session import get_async_db
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.attendance_service.models import MemberRef


async def require_admin_or_coach_for_session(
    session_id: uuid.UUID,
    current_user: AuthUser,
    db: AsyncSession,
) -> None:
    """
    Verify the user is either an admin or the coach assigned to the session's cohort.
    Raises 403 if not authorized.

    For cohort sessions: checks if user is the cohort's coach
    For non-cohort sessions: only admins allowed
    """
    # Admins and service roles can access any session
    if is_admin_or_service(current_user):
        return

    # Must have coach role
    if not current_user.has_role("coach"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin or coach privileges required",
        )

    # Get the session to find its cohort_id (via sessions-service)
    session_data = await get_session_by_id(
        str(session_id), calling_service="attendance"
    )
    if not session_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Session not found"
        )

    cohort_id = session_data.get("cohort_id")

    # Non-cohort sessions are admin-only
    if cohort_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins can view attendance for non-cohort sessions",
        )

    # Get member_id from auth_id (via members-service)
    member = await get_member_by_auth_id(
        current_user.user_id, calling_service="attendance"
    )
    if not member:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Member profile not found"
        )

    # Check if coach is assigned to this cohort (via academy-service)
    settings = get_settings()
    cohort_resp = await internal_get(
        service_url=settings.ACADEMY_SERVICE_URL,
        path=f"/internal/academy/cohorts/{cohort_id}",
        calling_service="attendance",
    )
    if cohort_resp.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Cohort not found"
        )
    cohort_data = cohort_resp.json()

    if str(cohort_data.get("coach_id")) != str(member["id"]):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not the assigned coach for this cohort",
        )


async def get_current_member(
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> MemberRef:
    query = select(MemberRef).where(MemberRef.auth_id == current_user.user_id)
    result = await db.execute(query)
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member profile not found. Please complete registration.",
        )
    return member


async def validate_session_access(
    session_data: dict,
    member_id: str,
    *,
    confirmed_booking: bool = False,
) -> None:
    """Build attendance-owned context and apply the shared access policy."""
    membership = None
    if not confirmed_booking and session_data.get("session_type") != "cohort_class":
        membership = await get_member_membership(
            member_id, calling_service="attendance"
        )
    member_payload = {
        "id": member_id,
        "member_id": member_id,
        **(membership or {}),
    }

    cohort_enrollment = None
    cohort_id = session_data.get("cohort_id")
    if cohort_id and not confirmed_booking:
        cohort_enrollment = await check_cohort_enrollment(
            str(cohort_id), member_id, calling_service="attendance"
        )

    pod_member_ids = None
    pod_id = session_data.get("pod_id")
    if pod_id and not confirmed_booking:
        pod = await get_pod_by_id(str(pod_id), calling_service="attendance")
        pod_member_ids = (pod or {}).get("active_member_ids") or []

    club_product_access = None
    if session_data.get("session_type") == "club" and not confirmed_booking:
        context_key = str(session_data.get("id") or "attendance-session")
        results = await check_club_access_batch(
            [
                {
                    "context_key": context_key,
                    "member_id": member_id,
                    "at": session_data["starts_at"],
                    "pool_id": session_data.get("pool_id"),
                    "pod_id": session_data.get("pod_id"),
                }
            ],
            calling_service="attendance",
        )
        club_product_access = bool(
            (results.get(context_key) or {}).get("allowed")
        )

    decision = evaluate_session_access(
        member_payload,
        session_data,
        now=utc_now(),
        cohort_enrollment=cohort_enrollment,
        pod_member_ids=pod_member_ids,
        confirmed_booking=confirmed_booking,
        club_product_access=club_product_access,
    )
    if not decision.sign_in_allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=denial_message(decision.reason),
        )
