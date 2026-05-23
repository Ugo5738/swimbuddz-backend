"""Internal service-to-service endpoints for sessions-service.

These endpoints are authenticated with service_role JWT only.
They are NOT exposed through the gateway — only other backend services
call them directly via Docker network.
"""

import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_service_role
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from libs.common.datetime_utils import utc_now
from services.sessions_service.models import (
    BookingChannel,
    Session,
    SessionBooking,
    SessionBookingStatus,
    SessionCoach,
    SessionStatus,
)
from services.sessions_service.schemas import (
    BookingConfirmRequest,
    BulkBookingRequest,
    BulkBookingResponse,
    SessionBookingResponse,
)

router = APIRouter(prefix="/internal/sessions", tags=["internal"])


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class SessionBasic(BaseModel):
    id: str
    title: str
    session_type: str
    status: str
    starts_at: str
    ends_at: str
    location_name: Optional[str] = None
    location_address: Optional[str] = None
    location: Optional[str] = None
    cohort_id: Optional[str] = None
    capacity: int
    # pool_fee is returned in KOBO (integer) for service-to-service use.
    # Consuming services: call kobo_to_bubbles(pool_fee) to get the Bubble charge.
    pool_fee: Optional[int] = None
    week_number: Optional[int] = None
    lesson_title: Optional[str] = None
    timezone: str = "Africa/Lagos"


class NextSessionResponse(BaseModel):
    starts_at: str
    title: str
    location_name: Optional[str] = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

# NOTE: Static path "/scheduled" must be registered before the
# parameterized "/{session_id}" to avoid route collision (FastAPI
# matches routes in definition order).


@router.get("/scheduled", response_model=List[SessionBasic])
async def get_scheduled_sessions(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Get scheduled sessions within a date range."""
    query = select(Session).where(Session.status == SessionStatus.SCHEDULED)
    if start_date:
        query = query.where(Session.starts_at >= start_date)
    if end_date:
        query = query.where(Session.starts_at < end_date)
    query = query.order_by(Session.starts_at.asc())
    result = await db.execute(query)
    sessions = result.scalars().all()
    return [
        SessionBasic(
            id=str(s.id),
            title=s.title,
            session_type=s.session_type.value,
            status=s.status.value,
            starts_at=s.starts_at.isoformat(),
            ends_at=s.ends_at.isoformat(),
            location_name=s.location_name,
            location_address=s.location_address,
            location=s.location.value if s.location else None,
            cohort_id=str(s.cohort_id) if s.cohort_id else None,
            capacity=s.capacity,
            pool_fee=s.pool_fee,
            week_number=s.week_number,
            lesson_title=s.lesson_title,
            timezone=s.timezone,
        )
        for s in sessions
    ]


# ---------------------------------------------------------------------------
# Reporting aggregation
# NOTE: Static path "/range-stats" must be registered before the
# parameterized "/{session_id}" to avoid route collision.
# ---------------------------------------------------------------------------


class SessionRangeStats(BaseModel):
    """Aggregated session stats for a date range."""

    total_sessions: int = 0
    by_type: dict | None = None
    new_members: int = 0  # placeholder — computed elsewhere


class SessionDetailedStats(BaseModel):
    """Extended session stats for quarterly reports."""

    total_sessions: int = 0
    total_pool_hours: float = 0.0
    by_type: dict | None = None
    most_active_location: str | None = None
    busiest_session_title: str | None = None
    busiest_session_attendance: int = 0
    most_popular_day: str | None = None
    most_popular_time_slot: str | None = None
    session_details: list[dict] | None = None


@router.get("/range-stats", response_model=SessionRangeStats)
async def get_session_range_stats(
    date_from: datetime = Query(..., alias="from"),
    date_to: datetime = Query(..., alias="to"),
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Get aggregated session stats within a date range.

    Used by the reporting service for quarterly community stats.
    """
    from collections import Counter

    result = await db.execute(
        select(Session).where(
            Session.starts_at >= date_from,
            Session.starts_at <= date_to,
            Session.status.in_(
                [
                    SessionStatus.SCHEDULED,
                    SessionStatus.COMPLETED,
                ]
            ),
        )
    )
    sessions = result.scalars().all()

    type_counts = Counter(
        s.session_type.value
        if hasattr(s.session_type, "value")
        else str(s.session_type)
        for s in sessions
    )

    return SessionRangeStats(
        total_sessions=len(sessions),
        by_type=dict(type_counts) if type_counts else None,
    )


@router.get("/detailed-stats", response_model=SessionDetailedStats)
async def get_session_detailed_stats(
    date_from: str = Query(..., alias="from"),
    date_to: str = Query(..., alias="to"),
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Get detailed session stats for quarterly reports.

    Returns pool hours, location rankings, busiest sessions, etc.
    Accepts ISO 8601 date strings (with or without timezone).
    """
    from collections import Counter
    from datetime import datetime as _dt

    # Parse date strings flexibly
    parsed_from = _dt.fromisoformat(date_from.replace("Z", "+00:00"))
    parsed_to = _dt.fromisoformat(date_to.replace("Z", "+00:00"))

    result = await db.execute(
        select(Session).where(
            Session.starts_at >= parsed_from,
            Session.starts_at <= parsed_to,
            Session.status.in_([SessionStatus.SCHEDULED, SessionStatus.COMPLETED]),
        )
    )
    sessions = result.scalars().all()

    if not sessions:
        return SessionDetailedStats()

    # Total pool hours (sum of session durations)
    total_hours = sum(
        (s.ends_at - s.starts_at).total_seconds() / 3600 for s in sessions
    )

    # Type breakdown
    type_counts = Counter(
        s.session_type.value
        if hasattr(s.session_type, "value")
        else str(s.session_type)
        for s in sessions
    )

    # Location ranking
    locations = [s.location_name for s in sessions if s.location_name]
    location_counts = Counter(locations)
    most_active = location_counts.most_common(1)[0][0] if location_counts else None

    # Day of week popularity
    DAYS = [
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday",
    ]
    day_counts = Counter(DAYS[s.starts_at.weekday()] for s in sessions)
    most_popular_day = day_counts.most_common(1)[0][0] if day_counts else None

    # Time slot popularity
    def time_slot(hour: int) -> str:
        if hour < 12:
            return "Morning (before noon)"
        elif hour < 17:
            return "Afternoon (noon-5pm)"
        return "Evening (after 5pm)"

    slot_counts = Counter(time_slot(s.starts_at.hour) for s in sessions)
    most_popular_slot = slot_counts.most_common(1)[0][0] if slot_counts else None

    # Session details for per-session info
    details = [
        {
            "id": str(s.id),
            "title": s.title,
            "hours": round((s.ends_at - s.starts_at).total_seconds() / 3600, 2),
            "location": s.location_name,
            "type": s.session_type.value
            if hasattr(s.session_type, "value")
            else str(s.session_type),
            "capacity": s.capacity,
        }
        for s in sessions
    ]

    return SessionDetailedStats(
        total_sessions=len(sessions),
        total_pool_hours=round(total_hours, 1),
        by_type=dict(type_counts) if type_counts else None,
        most_active_location=most_active,
        most_popular_day=most_popular_day,
        most_popular_time_slot=most_popular_slot,
        session_details=details,
    )


@router.get("/durations")
async def get_session_durations(
    ids: str = Query(..., description="Comma-separated session UUIDs"),
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Return duration in hours for a list of session IDs.

    Used by attendance service to compute per-member pool hours.
    """
    import uuid as _uuid

    session_ids = []
    for sid in ids.split(","):
        sid = sid.strip()
        if sid:
            try:
                session_ids.append(_uuid.UUID(sid))
            except ValueError:
                continue

    if not session_ids:
        return []

    result = await db.execute(select(Session).where(Session.id.in_(session_ids)))
    sessions = result.scalars().all()

    return [
        {
            "session_id": str(s.id),
            "duration_hours": round(
                (s.ends_at - s.starts_at).total_seconds() / 3600, 2
            ),
        }
        for s in sessions
    ]


# NOTE: Parameterized routes must come AFTER all static routes to avoid
# "durations", "detailed-stats", etc. being matched as {session_id}.


@router.get("/{session_id}", response_model=SessionBasic)
async def get_session_by_id(
    session_id: uuid.UUID,
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Look up a session by ID."""
    result = await db.execute(select(Session).where(Session.id == session_id))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return SessionBasic(
        id=str(session.id),
        title=session.title,
        session_type=session.session_type.value,
        status=session.status.value,
        starts_at=session.starts_at.isoformat(),
        ends_at=session.ends_at.isoformat(),
        location_name=session.location_name,
        location_address=session.location_address,
        location=session.location.value if session.location else None,
        cohort_id=str(session.cohort_id) if session.cohort_id else None,
        capacity=session.capacity,
        pool_fee=session.pool_fee,
        week_number=session.week_number,
        lesson_title=session.lesson_title,
        timezone=session.timezone,
    )


@router.get("/cohorts/{cohort_id}/next-session", response_model=NextSessionResponse)
async def get_next_session_for_cohort(
    cohort_id: uuid.UUID,
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Get the next upcoming session for a cohort."""
    now = utc_now()
    result = await db.execute(
        select(Session)
        .where(
            Session.cohort_id == cohort_id,
            Session.starts_at > now,
            Session.status == SessionStatus.SCHEDULED,
        )
        .order_by(Session.starts_at.asc())
        .limit(1)
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="No upcoming session found")
    return NextSessionResponse(
        starts_at=session.starts_at.isoformat(),
        title=session.title,
        location_name=session.location_name,
    )


@router.get("/cohorts/{cohort_id}/session-ids", response_model=List[str])
async def get_session_ids_for_cohort(
    cohort_id: uuid.UUID,
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Get all session IDs for a cohort."""
    result = await db.execute(
        select(Session.id)
        .where(Session.cohort_id == cohort_id)
        .order_by(Session.starts_at.asc())
    )
    return [str(row[0]) for row in result.all()]


@router.get("/cohorts/{cohort_id}/completed-session-ids", response_model=List[str])
async def get_completed_session_ids_for_cohort(
    cohort_id: uuid.UUID,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Get completed session IDs for a cohort, optionally filtered by date range."""
    query = select(Session.id).where(
        Session.cohort_id == cohort_id,
        Session.status == SessionStatus.COMPLETED,
    )
    if start_date:
        query = query.where(Session.starts_at >= start_date)
    if end_date:
        query = query.where(Session.starts_at <= end_date)
    query = query.order_by(Session.starts_at.asc())
    result = await db.execute(query)
    return [str(row[0]) for row in result.all()]


@router.get("/{session_id}/coaches", response_model=List[str])
async def get_session_coach_ids(
    session_id: uuid.UUID,
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Get coach member IDs for a session."""
    result = await db.execute(
        select(SessionCoach.coach_id).where(SessionCoach.session_id == session_id)
    )
    return [str(row[0]) for row in result.all()]


# ---------------------------------------------------------------------------
# A1 Phase 3.3: SessionBooking internal endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/{session_id}/bookings/by-member/{member_id}",
    response_model=SessionBookingResponse,
)
async def get_booking_for_session_member(
    session_id: uuid.UUID,
    member_id: uuid.UUID,
    status: Optional[str] = Query(
        None, description="Filter by booking status (e.g. 'confirmed')"
    ),
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Service-role lookup: SessionBooking for (session, member).

    Used by attendance_service's sign-in flow to link the AttendanceRecord
    being created back to its originating booking. 404 if no booking
    matches the filter — caller treats that as "walk-in" and continues.
    """
    query = select(SessionBooking).where(
        SessionBooking.session_id == session_id,
        SessionBooking.member_id == member_id,
    )
    if status:
        try:
            query = query.where(SessionBooking.status == SessionBookingStatus(status))
        except ValueError:
            raise HTTPException(status_code=422, detail=f"Invalid status={status}")
    booking = (await db.execute(query)).scalar_one_or_none()
    if booking is None:
        raise HTTPException(status_code=404, detail="No booking found")
    return booking


@router.get(
    "/bookings/confirmed",
    response_model=List[SessionBookingResponse],
)
async def list_confirmed_bookings_since(
    since: datetime = Query(..., description="Lower bound on booked_at (ISO 8601)"),
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Service-role: list CONFIRMED bookings since `since`.

    Used by attendance_service's nightly NO_SHOW sweep to find recent
    confirmed bookings that may need an ABSENT AttendanceRecord created.
    """
    query = (
        select(SessionBooking)
        .where(
            SessionBooking.status == SessionBookingStatus.CONFIRMED,
            SessionBooking.booked_at >= since,
        )
        .order_by(SessionBooking.booked_at.asc())
    )
    return (await db.execute(query)).scalars().all()


@router.post(
    "/bookings/{booking_id}/confirm",
    response_model=SessionBookingResponse,
)
async def internal_confirm_booking(
    booking_id: uuid.UUID,
    confirm_in: BookingConfirmRequest,
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Service-role variant of /sessions/bookings/{id}/confirm.

    Future: payments_service webhook calls this when a SESSION_BOOKING
    payment intent clears (so the booking gets confirmed even if the
    member closed the browser mid-checkout).
    """
    booking = (
        await db.execute(select(SessionBooking).where(SessionBooking.id == booking_id))
    ).scalar_one_or_none()
    if booking is None:
        raise HTTPException(status_code=404, detail="Booking not found")
    if booking.status == SessionBookingStatus.CONFIRMED:
        # Walk-in flow: admin recorded the booking as CONFIRMED at the pool,
        # member later paid via a generated Paystack link. Backfill the
        # payment linkage so reports can join booking → payment without
        # going through the metadata JSON. Only fill blanks — never
        # overwrite an existing link.
        updated = False
        if (
            confirm_in.payment_intent_id is not None
            and booking.payment_intent_id is None
        ):
            booking.payment_intent_id = confirm_in.payment_intent_id
            updated = True
        if (
            confirm_in.wallet_transaction_id is not None
            and booking.wallet_transaction_id is None
        ):
            booking.wallet_transaction_id = confirm_in.wallet_transaction_id
            updated = True
        if updated:
            await db.commit()
            await db.refresh(booking)
        return booking
    if booking.status != SessionBookingStatus.PENDING:
        raise HTTPException(
            status_code=422,
            detail=f"Cannot confirm a booking with status={booking.status.value}.",
        )
    booking.status = SessionBookingStatus.CONFIRMED
    booking.confirmed_at = utc_now()
    if confirm_in.payment_intent_id is not None:
        booking.payment_intent_id = confirm_in.payment_intent_id
    if confirm_in.wallet_transaction_id is not None:
        booking.wallet_transaction_id = confirm_in.wallet_transaction_id
    await db.commit()
    await db.refresh(booking)
    return booking


@router.post("/bookings/bulk", response_model=BulkBookingResponse)
async def bulk_create_bookings(
    payload: BulkBookingRequest,
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Service-role bulk-create for corporate-wellness onboarding.

    Each row is created at status=CONFIRMED (sponsor-paid up front),
    channel=CORPORATE_BULK, with corporate_program_id set. Idempotent:
    pre-existing (session, member) pairs are reported as `skipped` and
    the existing row is returned unchanged.
    """
    created_rows: list[SessionBooking] = []
    skipped = 0
    now = utc_now()

    for item in payload.items:
        existing = (
            await db.execute(
                select(SessionBooking).where(
                    SessionBooking.session_id == item.session_id,
                    SessionBooking.member_id == item.member_id,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            skipped += 1
            created_rows.append(existing)
            continue

        booking = SessionBooking(
            session_id=item.session_id,
            member_id=item.member_id,
            member_auth_id=item.member_auth_id,
            status=SessionBookingStatus.CONFIRMED,
            channel=BookingChannel.CORPORATE_BULK,
            fee_amount_kobo=item.fee_amount_kobo,
            corporate_program_id=payload.corporate_program_id,
            booked_at=now,
            confirmed_at=now,
        )
        db.add(booking)
        created_rows.append(booking)

    await db.commit()
    for booking in created_rows:
        await db.refresh(booking)

    return BulkBookingResponse(
        created=len(payload.items) - skipped,
        skipped=skipped,
        bookings=[
            SessionBookingResponse.model_validate(b, from_attributes=True)
            for b in created_rows
        ],
    )
