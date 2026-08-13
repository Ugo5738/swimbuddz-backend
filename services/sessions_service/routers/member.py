import time
import uuid
from datetime import datetime
from typing import List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

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
from libs.common.logging import get_logger
from libs.common.service_client import (
    cancel_opportunities_for_context,
    get_member_by_auth_id,
    internal_post,
)
from libs.common.session_access import denial_message
from libs.db.session import get_async_db
from services.sessions_service.models import (
    Session,
    SessionBooking,
    SessionBookingStatus,
    SessionCoach,
    SessionStatus,
    SessionType,
)
from services.sessions_service.schemas import (
    SessionCreate,
    SessionResponse,
    SessionUpdate,
)
from services.sessions_service.services.notifications import (
    trigger_session_published_notifications,
)
from services.sessions_service.services.pricing import (
    PRICING_KEYS,
    normalize_pricing_payload,
    pricing_payload_from_session,
)
from services.sessions_service.services.session_access import (
    evaluate_session_access_for_member,
    evaluate_session_access_from_context,
    get_member_session_access_payload,
    get_sessions_access_context,
)

router = APIRouter(prefix="/sessions", tags=["sessions"])
settings = get_settings()
logger = get_logger(__name__)


def _session_payload(session: Session, access=None) -> dict:
    payload = SessionResponse.model_validate(session).model_dump(mode="json")
    if access is not None:
        payload["access"] = {
            "required_tier": access.required_tier,
            "visible": access.visible,
            "bookable": access.bookable,
            "digest_eligible": access.digest_eligible,
            "prompt_eligible": access.prompt_eligible,
            "sign_in_allowed": access.sign_in_allowed,
            "sign_in_eligible": access.sign_in_eligible,
            "reason": access.reason,
            "message": denial_message(access.reason) if access.reason else None,
        }
    return payload


async def _access_member_payload_for_user(
    current_user: Optional[AuthUser],
) -> dict | None:
    if current_user is None or is_admin_or_service(current_user):
        return None

    try:
        member = await get_member_by_auth_id(
            current_user.user_id,
            calling_service="sessions",
        )
    except httpx.HTTPError as exc:
        logger.warning(
            "Could not resolve member for session access user=%s: %s",
            current_user.user_id,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not verify your session access. Please try again.",
        )

    if not member or not member.get("id"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Complete your member profile before accessing sessions.",
        )

    try:
        member_id = uuid.UUID(str(member["id"]))
    except ValueError:
        logger.error(
            "Members service returned invalid member id for user=%s",
            current_user.user_id,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not verify your session access. Please try again.",
        )

    return await get_member_session_access_payload(
        member_id=member_id,
        calling_service="sessions",
    )


async def _decorate_sessions_for_user(
    sessions: list[Session],
    current_user: Optional[AuthUser],
    db: AsyncSession,
) -> list[dict] | list[Session]:
    if not sessions:
        return []

    member_payload = await _access_member_payload_for_user(current_user)
    if member_payload is None:
        return sessions

    now = utc_now()
    confirmed_session_ids = set(
        (
            await db.execute(
                select(SessionBooking.session_id).where(
                    SessionBooking.member_id
                    == uuid.UUID(str(member_payload["member_id"])),
                    SessionBooking.session_id.in_([session.id for session in sessions]),
                    SessionBooking.status == SessionBookingStatus.CONFIRMED,
                )
            )
        )
        .scalars()
        .all()
    )
    cohort_access, pod_rosters = await get_sessions_access_context(
        sessions=sessions,
        member_payload=member_payload,
        confirmed_session_ids=confirmed_session_ids,
        calling_service="sessions",
    )
    decorated: list[dict] = []
    for session in sessions:
        access = evaluate_session_access_from_context(
            session=session,
            member_payload=member_payload,
            now=now,
            confirmed_booking=session.id in confirmed_session_ids,
            cohort_access=cohort_access,
            pod_rosters=pod_rosters,
        )
        decorated.append(_session_payload(session, access))
    return decorated


async def _decorate_session_for_user(
    session: Session,
    current_user: Optional[AuthUser],
    db: AsyncSession,
) -> dict | Session:
    member_payload = await _access_member_payload_for_user(current_user)
    if member_payload is None:
        return session

    confirmed_booking = (
        await db.execute(
            select(SessionBooking.id).where(
                SessionBooking.member_id == uuid.UUID(str(member_payload["member_id"])),
                SessionBooking.session_id == session.id,
                SessionBooking.status == SessionBookingStatus.CONFIRMED,
            )
        )
    ).scalar_one_or_none()
    access = await evaluate_session_access_for_member(
        session=session,
        member_payload=member_payload,
        now=utc_now(),
        calling_service="sessions",
        confirmed_booking=confirmed_booking is not None,
    )
    return _session_payload(session, access)


@router.get("/", response_model=List[SessionResponse])
async def list_sessions(
    response: Response,
    types: Optional[str] = None,
    cohort_id: Optional[uuid.UUID] = None,
    status_filter: Optional[SessionStatus] = Query(
        default=None,
        alias="status",
        description="Exact session status to return.",
    ),
    date_from: Optional[datetime] = Query(
        default=None,
        alias="from",
        description="Inclusive session start boundary (ISO-8601).",
    ),
    date_to: Optional[datetime] = Query(
        default=None,
        alias="to",
        description="Exclusive session start boundary (ISO-8601).",
    ),
    after: Optional[datetime] = Query(
        default=None,
        description="Deprecated alias for `from`.",
    ),
    before: Optional[datetime] = Query(
        default=None,
        description="Deprecated alias for `to`.",
    ),
    limit: int = Query(
        default=50,
        ge=1,
        le=100,
        description="Maximum rows returned (max 100).",
    ),
    offset: int = Query(
        default=0,
        ge=0,
        description="Rows to skip for backward-compatible pagination.",
    ),
    include_drafts: bool = Query(
        False, description="Include draft sessions (admin only)"
    ),
    current_user: Optional[AuthUser] = Depends(get_optional_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    List a bounded, chronologically sorted session window.

    With no explicit date/status/admin-draft filters, this endpoint returns
    upcoming scheduled/in-progress sessions. An explicit date window without a
    status returns every published status in that window. `from` is inclusive
    and `to` is exclusive. `after`/`before` remain supported for older clients.
    """
    started_at = time.perf_counter()
    query = select(Session)
    effective_from = date_from or after
    effective_to = date_to or before
    has_explicit_window = any(
        value is not None for value in (date_from, date_to, after, before)
    )

    # Filter out DRAFT sessions unless an admin explicitly requests them.
    # Supabase user tokens typically have role=authenticated; custom roles
    # live under app_metadata, so use the shared helper.
    is_admin = bool(current_user and is_admin_or_service(current_user))
    if not (is_admin and include_drafts):
        query = query.where(Session.status != SessionStatus.DRAFT)

    if status_filter is not None:
        query = query.where(Session.status == status_filter)
    elif not has_explicit_window and not (is_admin and include_drafts):
        query = query.where(
            Session.status.in_([SessionStatus.SCHEDULED, SessionStatus.IN_PROGRESS])
        )

    if types:
        type_values = [t.strip() for t in types.split(",") if t.strip()]
        if type_values:
            query = query.where(Session.session_type.in_(type_values))

    if cohort_id:
        query = query.where(Session.cohort_id == cohort_id)

    if (
        effective_from is not None
        and effective_to is not None
        and effective_from >= effective_to
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="`from` must be earlier than `to`.",
        )

    if (
        not has_explicit_window
        and status_filter is None
        and not (is_admin and include_drafts)
    ):
        effective_from = utc_now()

    if effective_from is not None:
        query = query.where(Session.starts_at >= effective_from)
    if effective_to is not None:
        query = query.where(Session.starts_at < effective_to)

    query = (
        query.order_by(Session.starts_at.asc(), Session.id.asc())
        .offset(offset)
        .limit(limit + 1)
    )
    db_started_at = time.perf_counter()
    result = await db.execute(query)
    rows = list(result.scalars().all())
    db_ms = (time.perf_counter() - db_started_at) * 1000
    has_more = len(rows) > limit
    sessions = rows[:limit]

    access_started_at = time.perf_counter()
    decorated = await _decorate_sessions_for_user(sessions, current_user, db)
    access_ms = (time.perf_counter() - access_started_at) * 1000
    total_ms = (time.perf_counter() - started_at) * 1000

    response.headers["X-Result-Count"] = str(len(sessions))
    response.headers["X-Has-More"] = str(has_more).lower()
    if has_more:
        response.headers["X-Next-Offset"] = str(offset + limit)
    response.headers["Server-Timing"] = (
        f"sessions_db;dur={db_ms:.2f}, "
        f"access_enrichment;dur={access_ms:.2f}, "
        f"sessions_total;dur={total_ms:.2f}"
    )

    logger.info(
        "Sessions list completed",
        extra={
            "extra_fields": {
                "result_count": len(sessions),
                "has_more": has_more,
                "offset": offset,
                "limit": limit,
                "db_ms": round(db_ms, 2),
                "access_ms": round(access_ms, 2),
                "duration_ms": round(total_ms, 2),
            }
        },
    )
    return decorated


@router.get("/stats")
async def get_session_stats(
    db: AsyncSession = Depends(get_async_db),
):
    """
    Get session statistics.
    """
    now = utc_now()
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
            f"{settings.ACADEMY_SERVICE_URL}/internal/academy/coaches/{member_id}/cohort-ids",
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
    current_user: Optional[AuthUser] = Depends(get_optional_user),
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
    if session.status == SessionStatus.DRAFT and not (
        current_user and is_admin_or_service(current_user)
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )
    return await _decorate_session_for_user(session, current_user, db)


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

    # Freeze the editable cost lines and margin before converting the final
    # per-attendee booking price to kobo.
    session_data.update(normalize_pricing_payload(session_data))

    # Convert naira fee inputs (float) to kobo (int) for DB storage.
    session_data["pool_fee"] = round((session_data.get("pool_fee") or 0.0) * 100)
    guest_fee = session_data.pop("guest_fee", None)
    community_dropin_fee = session_data.pop("community_dropin_fee", None)
    session_data["guest_fee_kobo"] = (
        round(guest_fee * 100) if guest_fee is not None else None
    )
    session_data["community_dropin_fee_kobo"] = (
        round(community_dropin_fee * 100) if community_dropin_fee is not None else None
    )
    session_data["ride_share_fee"] = round(
        (session_data.get("ride_share_fee") or 0.0) * 100
    )

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

    if session.status == SessionStatus.SCHEDULED:
        await trigger_session_published_notifications(
            session_id=session.id,
            starts_at=session.starts_at,
        )

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
    # Lock the row for the transaction so two concurrent publish calls can't
    # both run the DRAFT→SCHEDULED transition and double-fire member
    # notifications. The loser of the race observes SCHEDULED below and no-ops.
    query = select(Session).where(Session.id == session_id).with_for_update()
    result = await db.execute(query)
    session = result.scalar_one_or_none()

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    # Idempotent publish: a session that's already SCHEDULED is a success
    # no-op. Cohort-class sessions are auto-scheduled at creation, and
    # retries / double-clicks can re-hit this endpoint — none of those should
    # surface a 400 or re-send notifications. Return the session unchanged.
    if session.status == SessionStatus.SCHEDULED:
        return session

    # Any other non-DRAFT state (in_progress / completed / cancelled) is a
    # genuine conflict: those can't transition into a freshly published session.
    if session.status != SessionStatus.DRAFT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Session is {session.status.value}, cannot publish",
        )

    now = utc_now()

    # Check if session start time has already passed
    if session.starts_at <= now:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot publish a session that has already started or passed",
        )

    # Update session status
    session.status = SessionStatus.SCHEDULED
    session.published_at = now

    await db.commit()
    await db.refresh(session)

    await trigger_session_published_notifications(
        session_id=session.id,
        starts_at=session.starts_at,
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

    # Cancel pending notifications and send cancellation emails via HTTP.
    # Best-effort: notification failures must not block the cancellation response.
    settings = get_settings()
    try:
        await internal_post(
            service_url=settings.COMMUNICATIONS_SERVICE_URL,
            path="/internal/communications/session-cancelled",
            calling_service="sessions",
            json={
                "session_id": str(session.id),
                "cancellation_reason": cancellation_reason or "",
            },
        )
    except Exception as exc:
        import logging

        logger = logging.getLogger(__name__)
        logger.error(
            "Failed to trigger cancel notifications for session %s: %s", session_id, exc
        )

    # Cascade-cancel any volunteer opportunities tied to this session. Best
    # effort: a volunteer-service outage must not block the cancellation
    # response. See docs/design/VOLUNTEER_OPPORTUNITY_CONTEXT_DESIGN.md.
    try:
        await cancel_opportunities_for_context(
            calling_service="sessions",
            session_id=str(session.id),
            reason=cancellation_reason or "Session cancelled",
        )
    except Exception as exc:
        import logging

        logger = logging.getLogger(__name__)
        logger.error(
            "Failed to cascade-cancel volunteer opportunities for session %s: %s",
            session_id,
            exc,
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

    if PRICING_KEYS & set(update_data):
        pricing_payload = pricing_payload_from_session(session)
        pricing_payload.update(
            {
                key: value
                for key, value in update_data.items()
                if key in PRICING_KEYS or key == "capacity"
            }
        )
        normalized_pricing = normalize_pricing_payload(pricing_payload)
        update_data.update(normalized_pricing)

    # Convert naira fee inputs (float) to kobo (int) for DB storage.
    if "pool_fee" in update_data and update_data["pool_fee"] is not None:
        update_data["pool_fee"] = round(update_data["pool_fee"] * 100)
    if "guest_fee" in update_data:
        value = update_data.pop("guest_fee")
        update_data["guest_fee_kobo"] = (
            round(value * 100) if value is not None else None
        )
    if "community_dropin_fee" in update_data:
        value = update_data.pop("community_dropin_fee")
        update_data["community_dropin_fee_kobo"] = (
            round(value * 100) if value is not None else None
        )
    if "ride_share_fee" in update_data and update_data["ride_share_fee"] is not None:
        update_data["ride_share_fee"] = round(update_data["ride_share_fee"] * 100)

    for field, value in update_data.items():
        setattr(session, field, value)

    became_scheduled = (
        old_status == SessionStatus.DRAFT and session.status == SessionStatus.SCHEDULED
    )
    if became_scheduled:
        if session.published_at is None:
            session.published_at = utc_now()
    elif (
        old_status == SessionStatus.SCHEDULED and session.status == SessionStatus.DRAFT
    ):
        session.published_at = None

    db.add(session)
    await db.commit()
    await db.refresh(session)

    if became_scheduled:
        await trigger_session_published_notifications(
            session_id=session.id,
            starts_at=session.starts_at,
        )

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
