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
    check_cohort_enrollment,
    get_member_by_auth_id,
    get_member_membership,
    get_session_by_id,
    internal_get,
)
from libs.common.datetime_utils import utc_now
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
) -> None:
    """Enforce tier-based session access control.

    Raises HTTPException with friendly messages if the member's membership
    tier does not permit access to this session type.

    Access rules:
    - cohort_class: only enrolled cohort members (not suspended)
    - club: only members with active club tier
    - community/event: any member with an active membership
    - one_on_one/group_booking: no tier check (future booking system)
    """
    from datetime import datetime

    session_type = session_data.get("session_type")

    if session_type == "cohort_class":
        cohort_id = session_data.get("cohort_id")
        if not cohort_id:
            # Cohort session without a cohort_id — shouldn't happen, allow through
            return

        enrollment = await check_cohort_enrollment(
            str(cohort_id), member_id, calling_service="attendance"
        )
        if not enrollment or not enrollment.get("enrolled"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "This academy cohort follows a structured curriculum — "
                    "members start and progress together. "
                    "Check swimbuddz.com for the next cohort enrollment."
                ),
            )
        if enrollment.get("access_suspended"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Your access to this cohort is currently suspended. "
                    "Please contact the SwimBuddz team for more information."
                ),
            )

    elif session_type == "club":
        membership = await get_member_membership(
            member_id, calling_service="attendance"
        )
        if not membership:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "This is a club session for members training together weekly. "
                    "Join the club to participate — visit swimbuddz.com for details."
                ),
            )
        active_tiers = membership.get("active_tiers") or []
        club_paid_until = membership.get("club_paid_until")

        has_club = "club" in active_tiers
        club_current = False
        if club_paid_until:
            try:
                paid_until = datetime.fromisoformat(club_paid_until)
                club_current = paid_until > utc_now()
            except (ValueError, TypeError):
                pass

        if not has_club or not club_current:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "This is a club session for members training together weekly. "
                    "Join the club to participate — plans start at ₦42,500/quarter. "
                    "Visit swimbuddz.com or ask any club member for details!"
                ),
            )

    elif session_type in ("community", "event"):
        membership = await get_member_membership(
            member_id, calling_service="attendance"
        )
        if not membership:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Welcome to SwimBuddz! You need an active membership to sign "
                    "in to sessions. Community membership starts at ₦20,000/year "
                    "— visit swimbuddz.com to get started."
                ),
            )
        active_tiers = membership.get("active_tiers") or []
        if not active_tiers:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Your membership isn't currently active. "
                    "Renew at swimbuddz.com to sign in to sessions."
                ),
            )

    # one_on_one, group_booking — no tier check (future booking system)
