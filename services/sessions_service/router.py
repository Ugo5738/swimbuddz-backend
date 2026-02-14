import uuid
from datetime import datetime, timezone
from typing import List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from libs.auth.dependencies import (
    _service_role_jwt,
    get_optional_user,
    is_admin_or_service,
    require_admin,
    require_coach,
)
from libs.auth.models import AuthUser
from libs.common.config import get_settings
from libs.common.datetime_utils import utc_now
from libs.db.session import get_async_db
from services.sessions_service.models import (
    Session,
    SessionCoach,
    SessionStatus,
    SessionType,
)
from services.sessions_service.schemas import (
    SessionCreate,
    SessionResponse,
    SessionUpdate,
)
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

settings = get_settings()

router = APIRouter(prefix="/sessions", tags=["sessions"])

# Short notice threshold in hours
SHORT_NOTICE_THRESHOLD_HOURS = 6


@router.get("/", response_model=List[SessionResponse])
async def list_sessions(
    types: Optional[str] = None,
    cohort_id: Optional[uuid.UUID] = None,
    include_drafts: bool = Query(
        False, description="Include draft sessions (admin only)"
    ),
    current_user: Optional[AuthUser] = Depends(get_optional_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    List all upcoming sessions. Optional `types` filter is a comma-separated list
    of SessionType values (e.g., "club,community"). Optional `cohort_id` filter
    returns only sessions for that cohort.

    Draft sessions are only visible to admins with include_drafts=true.
    """
    query = select(Session).order_by(Session.starts_at.asc())

    # Filter out DRAFT sessions unless an admin explicitly requests them.
    # Supabase user tokens typically have role=authenticated; custom roles
    # live under app_metadata, so use the shared helper.
    is_admin = bool(current_user and is_admin_or_service(current_user))
    if not (is_admin and include_drafts):
        query = query.where(Session.status != SessionStatus.DRAFT)

    if types:
        type_values = [t.strip() for t in types.split(",") if t.strip()]
        if type_values:
            query = query.where(Session.session_type.in_(type_values))

    if cohort_id:
        query = query.where(Session.cohort_id == cohort_id)

    result = await db.execute(query)
    return result.scalars().all()


@router.get("/stats")
async def get_session_stats(
    db: AsyncSession = Depends(get_async_db),
):
    """
    Get session statistics.
    """
    now = datetime.now(timezone.utc)
    query = select(func.count(Session.id)).where(Session.starts_at > now)
    result = await db.execute(query)
    upcoming_sessions_count = result.scalar_one() or 0

    return {"upcoming_sessions_count": upcoming_sessions_count}


@router.get("/coach/me", response_model=List[SessionResponse])
async def list_my_coach_sessions(
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    current_user: AuthUser = Depends(require_coach),
    db: AsyncSession = Depends(get_async_db),
):
    """
    List sessions for the current coach. Includes:
    - Sessions linked to cohorts where the coach is assigned
    - Sessions where the coach is listed in session_coaches

    Optional date range filters (ISO format: YYYY-MM-DD).
    """
    # 1. Resolve Member ID via members-service (avoid cross-service DB reads)
    headers = {"Authorization": f"Bearer {_service_role_jwt('sessions')}"}
    async with httpx.AsyncClient(timeout=10) as client:
        member_resp = await client.get(
            f"{settings.MEMBERS_SERVICE_URL}/members/by-auth/{current_user.user_id}",
            headers=headers,
        )

        if member_resp.status_code == 404:
            raise HTTPException(status_code=404, detail="Member profile not found")
        if not member_resp.is_success:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Failed to resolve member profile",
            )

        member_id = member_resp.json().get("id")
        if not member_id:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Members service returned invalid member payload",
            )

        # 2. Resolve cohort IDs via academy-service (avoid cross-service DB reads)
        cohorts_resp = await client.get(
            f"{settings.ACADEMY_SERVICE_URL}/academy/internal/coaches/{member_id}/cohort-ids",
            headers=headers,
        )
        if not cohorts_resp.is_success:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Failed to resolve coach cohort assignments",
            )
        cohort_ids = cohorts_resp.json() or []

    # 3. Get session IDs where coach is directly assigned
    session_coach_query = select(SessionCoach.session_id).where(
        SessionCoach.coach_id == member_id
    )
    session_coach_result = await db.execute(session_coach_query)
    direct_session_ids = [row[0] for row in session_coach_result.fetchall()]

    # 4. Build combined query
    conditions = []
    if cohort_ids:
        conditions.append(Session.cohort_id.in_(cohort_ids))
    if direct_session_ids:
        conditions.append(Session.id.in_(direct_session_ids))

    if not conditions:
        return []

    query = select(Session).where(or_(*conditions))

    # 5. Apply date filters
    if from_date:
        try:
            from_dt = datetime.fromisoformat(from_date)
            query = query.where(Session.starts_at >= from_dt)
        except ValueError:
            pass

    if to_date:
        try:
            to_dt = datetime.fromisoformat(to_date)
            query = query.where(Session.starts_at <= to_dt)
        except ValueError:
            pass

    query = query.order_by(Session.starts_at.asc())
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/{session_id}", response_model=SessionResponse)
async def get_session(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    """
    Get details of a specific session.
    """
    query = select(Session).where(Session.id == session_id)
    result = await db.execute(query)
    session = result.scalar_one_or_none()

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )
    return session


@router.post("/", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
async def create_session(
    session_in: SessionCreate,
    current_user: AuthUser = Depends(require_admin),  # Only admins can create sessions
    db: AsyncSession = Depends(get_async_db),
):
    """
    Create a new session (Admin only).

    Sessions are created in DRAFT status by default. Use the publish endpoint
    to make them visible to members and trigger notifications.
    """
    # Validate cohort_id exists via academy-service (avoid cross-service DB reads)
    if session_in.cohort_id:
        headers = {"Authorization": f"Bearer {_service_role_jwt('sessions')}"}
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{settings.ACADEMY_SERVICE_URL}/academy/cohorts/{session_in.cohort_id}",
                headers=headers,
            )
            if resp.status_code == 404:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid cohort_id: cohort does not exist",
                )
            if not resp.is_success:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="Failed to validate cohort_id",
                )

    session_data = session_in.model_dump()
    # Remove ride_share_areas if present in input, though schema should handle it
    session_data.pop("ride_share_areas", None)

    # Default statuses:
    # - Cohort sessions should be immediately visible to enrolled members.
    # - Other session types default to DRAFT so admins can review before publish.
    if "status" not in session_data or session_data["status"] is None:
        if session_in.session_type == SessionType.COHORT_CLASS and session_in.cohort_id:
            session_data["status"] = SessionStatus.SCHEDULED
        else:
            session_data["status"] = SessionStatus.DRAFT

    # Keep published_at consistent when creating scheduled sessions directly.
    if session_data.get("status") == SessionStatus.SCHEDULED:
        session_data.setdefault("published_at", utc_now())
    elif session_data.get("status") == SessionStatus.DRAFT:
        session_data["published_at"] = None

    session = Session(**session_data)
    db.add(session)
    await db.commit()
    await db.refresh(session)

    return session


@router.post("/{session_id}/publish", response_model=SessionResponse)
async def publish_session(
    session_id: uuid.UUID,
    short_notice_message: Optional[str] = Query(
        None,
        description="Optional message explaining short notice (shown in announcement)",
    ),
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Publish a draft session, making it visible to members.

    This transitions the session from DRAFT to SCHEDULED status, sets the
    published_at timestamp, and triggers notifications:
    - Immediate announcement to subscribed members
    - Scheduled reminders (24h, 3h, 1h before start)

    If the session starts within 6 hours, it's marked as "short notice" and
    only applicable reminders are scheduled.
    """
    query = select(Session).where(Session.id == session_id)
    result = await db.execute(query)
    session = result.scalar_one_or_none()

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    if session.status != SessionStatus.DRAFT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Session is already {session.status.value}, cannot publish",
        )

    now = utc_now()

    # Check if session start time has already passed
    if session.starts_at <= now:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot publish a session that has already started or passed",
        )

    # Determine if this is short notice
    hours_until_start = (session.starts_at - now).total_seconds() / 3600
    is_short_notice = hours_until_start < SHORT_NOTICE_THRESHOLD_HOURS

    # Update session status
    session.status = SessionStatus.SCHEDULED
    session.published_at = now

    await db.commit()
    await db.refresh(session)

    # Trigger notifications asynchronously
    # Import here to avoid circular imports
    from services.communications_service.tasks import (
        schedule_session_notifications,
        send_session_announcement,
    )

    # Schedule reminders (24h, 3h, 1h)
    await schedule_session_notifications(
        session_id=session.id,
        is_short_notice=is_short_notice,
    )

    # Send immediate announcement
    await send_session_announcement(
        session_id=session.id,
        short_notice_message=short_notice_message or "",
    )

    return session


@router.post("/{session_id}/cancel", response_model=SessionResponse)
async def cancel_session(
    session_id: uuid.UUID,
    cancellation_reason: Optional[str] = Query(
        None, description="Reason for cancellation (shown in notification)"
    ),
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Cancel a session and notify registered attendees.

    This transitions the session to CANCELLED status and sends cancellation
    notifications to all registered attendees and coaches.
    """
    query = select(Session).where(Session.id == session_id)
    result = await db.execute(query)
    session = result.scalar_one_or_none()

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    if session.status == SessionStatus.CANCELLED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Session is already cancelled",
        )

    if session.status == SessionStatus.COMPLETED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot cancel a completed session",
        )

    # Update session status
    session.status = SessionStatus.CANCELLED

    await db.commit()
    await db.refresh(session)

    # Cancel pending notifications and send cancellation emails
    from services.communications_service.tasks import cancel_session_notifications

    await cancel_session_notifications(
        session_id=session.id,
        cancellation_reason=cancellation_reason or "",
    )

    return session


@router.patch("/{session_id}", response_model=SessionResponse)
async def update_session(
    session_id: uuid.UUID,
    session_in: SessionUpdate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Update a session.
    """
    query = select(Session).where(Session.id == session_id)
    result = await db.execute(query)
    session = result.scalar_one_or_none()

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    old_status = session.status
    update_data = session_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(session, field, value)

    # If admins schedule a draft directly (without calling /publish), treat it
    # as published but do not trigger announcements.
    if old_status == SessionStatus.DRAFT and session.status == SessionStatus.SCHEDULED:
        if session.published_at is None:
            session.published_at = utc_now()
    elif (
        old_status == SessionStatus.SCHEDULED and session.status == SessionStatus.DRAFT
    ):
        session.published_at = None

    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


@router.delete("/by-cohort/{cohort_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_sessions_for_cohort(
    cohort_id: uuid.UUID,
    _: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Delete all sessions (and related rows) for a cohort."""
    session_ids = select(Session.id).where(Session.cohort_id == cohort_id)
    await db.execute(
        delete(SessionCoach).where(SessionCoach.session_id.in_(session_ids))
    )
    await db.execute(delete(Session).where(Session.cohort_id == cohort_id))
    await db.commit()
    return None


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Delete a session.
    """
    query = select(Session).where(Session.id == session_id)
    result = await db.execute(query)
    session = result.scalar_one_or_none()

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    await db.delete(session)
    await db.commit()
