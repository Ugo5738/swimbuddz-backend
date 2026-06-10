"""Make-up scheduling endpoints (Phase 0).

Exposes the bookable-slot computation for the admin make-up booking screen:
given a coach, a learner, and a date window, return the coach's open slots
(availability − blackouts − already-booked) with spacing flags for that learner.
See docs/design/AVAILABILITY_AND_MAKEUP_SCHEDULING_DESIGN.md §8.

Booking creation / confirmation (writing MakeupBooking rows) is Phase 1.
"""

import uuid
from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from libs.auth.dependencies import get_current_user, require_admin
from libs.auth.models import AuthUser
from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from libs.common.service_client import (
    complete_makeup_obligation,
    get_coach_availability,
    get_member_by_auth_id,
    get_member_by_id,
    schedule_makeup_obligation,
)
from libs.db.session import get_async_db
from services.sessions_service.models import (
    BookingChannel,
    MakeupBlockKind,
    MakeupBooking,
    MakeupLearnerType,
    MakeupOrigin,
    MakeupStatus,
    Session,
    SessionBooking,
    SessionBookingStatus,
    SessionCoach,
    SessionStatus,
    SessionType,
)
from services.sessions_service.schemas import (
    BookableSlotResponse,
    BookableSlotsResponse,
    MakeupBookingCreate,
    MakeupBookingResponse,
    MakeupOpenSlotCreate,
    MakeupRequestCreate,
)
from services.sessions_service.services.makeup_scheduling import (
    MAKEUP_WINDOW_DAYS,
    CoachSession,
    compute_bookable_slots,
    makeup_window_end,
    notice_hours,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/makeups", tags=["makeups"])
logger = get_logger(__name__)

_MAX_WINDOW_DAYS = 60
_HOLD_MINUTES = 30


async def _resolve_member_id(auth_id: str | None) -> uuid.UUID:
    """Resolve the authenticated user's member id via members_service."""
    if not auth_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication"
        )
    member = await get_member_by_auth_id(auth_id, calling_service="sessions")
    if not member or not member.get("id"):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Member not found."
        )
    return uuid.UUID(member["id"])


async def _compute_bookable_slots_for(
    db: AsyncSession,
    *,
    coach_id: uuid.UUID,
    learner_id: uuid.UUID,
    from_date: date,
    to_date: date,
) -> BookableSlotsResponse:
    """Compute open + joinable make-up options for a coach + learner over a window.

    Shared by the admin bookable-slots endpoint and the learner self-serve
    options endpoint. Spacing violations are flagged, not removed (D2).
    """
    avail = await get_coach_availability(str(coach_id), calling_service="sessions")
    calendar = (avail or {}).get("availability_calendar") or {}
    availability_set = bool(calendar)
    min_hours = (avail or {}).get("min_hours_between_sessions")

    lo = datetime.combine(from_date - timedelta(days=1), time.min, tzinfo=timezone.utc)
    hi = datetime.combine(to_date + timedelta(days=2), time.min, tzinfo=timezone.utc)

    session_rows = (
        await db.execute(
            select(
                Session.id,
                Session.title,
                Session.capacity,
                Session.starts_at,
                Session.ends_at,
            )
            .join(SessionCoach, SessionCoach.session_id == Session.id)
            .where(SessionCoach.coach_id == coach_id)
            .where(
                Session.status.in_([SessionStatus.SCHEDULED, SessionStatus.IN_PROGRESS])
            )
            .where(Session.ends_at >= lo)
            .where(Session.starts_at < hi)
        )
    ).all()

    booked: dict[uuid.UUID, int] = {}
    session_ids = [r.id for r in session_rows]
    if session_ids:
        count_rows = (
            await db.execute(
                select(SessionBooking.session_id, func.count())
                .where(SessionBooking.session_id.in_(session_ids))
                .where(SessionBooking.status == SessionBookingStatus.CONFIRMED)
                .group_by(SessionBooking.session_id)
            )
        ).all()
        booked = {sid: count for sid, count in count_rows}

    coach_sessions = [
        CoachSession(
            start=r.starts_at,
            end=r.ends_at,
            session_id=str(r.id),
            title=r.title,
            capacity=r.capacity,
            booked_count=booked.get(r.id, 0),
        )
        for r in session_rows
    ]

    learner_rows = (
        await db.execute(
            select(Session.starts_at)
            .join(SessionBooking, SessionBooking.session_id == Session.id)
            .where(SessionBooking.member_id == learner_id)
            .where(SessionBooking.status == SessionBookingStatus.CONFIRMED)
            .where(Session.starts_at >= lo)
            .where(Session.starts_at < hi)
        )
    ).all()
    learner_sessions = [r[0] for r in learner_rows]

    slots = compute_bookable_slots(
        calendar,
        window_start=from_date,
        window_end=to_date,
        coach_sessions=coach_sessions,
        learner_sessions=learner_sessions,
        min_hours_between=min_hours,
    )
    return BookableSlotsResponse(
        coach_id=str(coach_id),
        learner_id=str(learner_id),
        availability_set=availability_set,
        slots=[
            BookableSlotResponse(
                start=s.start,
                end=s.end,
                kind=s.kind,
                session_id=s.session_id,
                session_title=s.session_title,
                spots_left=s.spots_left,
                ok=s.ok,
                warnings=s.warnings,
            )
            for s in slots
        ],
    )


async def _book_learner_into_session(
    db: AsyncSession,
    *,
    session_id: uuid.UUID,
    learner_member_id: uuid.UUID,
    learner_auth_id: str,
) -> None:
    """Idempotent CONFIRMED SessionBooking for the learner; raises 409 if full."""
    existing = (
        await db.execute(
            select(SessionBooking).where(
                SessionBooking.session_id == session_id,
                SessionBooking.member_id == learner_member_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.status != SessionBookingStatus.CONFIRMED:
            existing.status = SessionBookingStatus.CONFIRMED
            existing.confirmed_at = utc_now()
            existing.expires_at = None
        return
    session = (
        await db.execute(select(Session).where(Session.id == session_id))
    ).scalar_one_or_none()
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Session not found."
        )
    confirmed_count = (
        await db.execute(
            select(func.count())
            .select_from(SessionBooking)
            .where(
                SessionBooking.session_id == session_id,
                SessionBooking.status == SessionBookingStatus.CONFIRMED,
            )
        )
    ).scalar_one()
    if confirmed_count >= session.capacity:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="The chosen session is full."
        )
    db.add(
        SessionBooking(
            session_id=session_id,
            member_id=learner_member_id,
            member_auth_id=learner_auth_id,
            status=SessionBookingStatus.CONFIRMED,
            channel=BookingChannel.ADMIN,
            confirmed_at=utc_now(),
        )
    )


def _require_reschedule_reason(origin: MakeupOrigin, reason: str | None) -> None:
    """A reschedule needs a cogent reason even with notice (policy §4 / 1b)."""
    if origin == MakeupOrigin.LEARNER_RESCHEDULE and not (reason or "").strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="A reschedule needs a reason.",
        )


def _as_utc(dt: datetime) -> datetime:
    """Treat a naive datetime as UTC; leave tz-aware ones untouched."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


async def _assert_no_outstanding_makeup(
    db: AsyncSession, learner_member_id: uuid.UUID
) -> None:
    """One outstanding make-up at a time (policy §4); raises 409 if one exists."""
    open_count = (
        await db.execute(
            select(func.count())
            .select_from(MakeupBooking)
            .where(
                MakeupBooking.learner_member_id == learner_member_id,
                MakeupBooking.status.in_(
                    [MakeupStatus.REQUESTED, MakeupStatus.HELD, MakeupStatus.CONFIRMED]
                ),
            )
        )
    ).scalar_one()
    if open_count:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Learner already has an outstanding make-up; clear it first.",
        )


async def _confirm_makeup_against_session(
    db: AsyncSession,
    *,
    data: MakeupBookingCreate,
    session: Session,
) -> MakeupBooking:
    """Shared confirm tail for the confirm-existing and open-slot paths.

    Assumes ``session`` exists and the coach (``data.coach_member_id``) is
    assigned to it. Enforces one-outstanding, the 14-day window, and one-grace;
    books the learner in (CONFIRMED, capacity-checked); writes a CONFIRMED
    MakeupBooking; and flips the linked obligation (best-effort).
    """
    # One outstanding make-up at a time (policy §4).
    await _assert_no_outstanding_makeup(db, data.learner_member_id)

    # 14-day window + cohort-term block, derived from the missed session (§4).
    notice = None
    block_kind = data.block_kind
    block_id = data.block_id
    if data.original_session_id is not None:
        original = (
            await db.execute(
                select(Session.starts_at, Session.cohort_id).where(
                    Session.id == data.original_session_id
                )
            )
        ).one_or_none()
        if original is not None:
            original_start, original_cohort_id = original
            if session.starts_at.date() > makeup_window_end(original_start.date()):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Make-up is past the {MAKEUP_WINDOW_DAYS}-day window.",
                )
            notice = int(notice_hours(utc_now(), original_start))
            # Auto-derive the cohort-term block when the caller didn't supply one.
            if block_id is None and original_cohort_id is not None:
                block_kind = MakeupBlockKind.COHORT_TERM
                block_id = original_cohort_id

    # One grace per block (policy §4).
    if data.used_grace and block_id is not None:
        grace_used = (
            await db.execute(
                select(func.count())
                .select_from(MakeupBooking)
                .where(
                    MakeupBooking.learner_member_id == data.learner_member_id,
                    MakeupBooking.block_id == block_id,
                    MakeupBooking.used_grace.is_(True),
                )
            )
        ).scalar_one()
        if grace_used:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Grace already used for this block.",
            )

    # Resolve the learner's auth_id (needed on the booking for attendance).
    learner = await get_member_by_id(
        str(data.learner_member_id), calling_service="sessions"
    )
    if not learner or not learner.get("auth_id"):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Learner not found."
        )

    # Book the learner into the session (CONFIRMED, idempotent + capacity-checked).
    await _book_learner_into_session(
        db,
        session_id=session.id,
        learner_member_id=data.learner_member_id,
        learner_auth_id=learner["auth_id"],
    )

    learner_type = (
        MakeupLearnerType.ONE_ON_ONE
        if block_kind == MakeupBlockKind.LESSON_PACKAGE
        else MakeupLearnerType.COHORT
    )
    makeup = MakeupBooking(
        learner_member_id=data.learner_member_id,
        coach_member_id=data.coach_member_id,
        learner_type=learner_type,
        block_kind=block_kind,
        block_id=block_id,
        origin=data.origin,
        original_session_id=data.original_session_id,
        scheduled_session_id=session.id,
        status=MakeupStatus.CONFIRMED,
        used_grace=data.used_grace,
        notice_hours_at_request=notice,
        spacing_overridden_by=(
            data.coach_member_id if data.spacing_overridden else None
        ),
        obligation_id=data.obligation_id,
        notes=data.reason,
    )
    db.add(makeup)
    await db.commit()
    await db.refresh(makeup)

    # Flip the linked cohort payout obligation to SCHEDULED (best-effort; the
    # obligation_id link lets admin reconcile if this call fails). Design §9.
    if data.obligation_id is not None:
        try:
            await schedule_makeup_obligation(
                str(data.obligation_id),
                str(session.id),
                calling_service="sessions",
                notes=data.reason,
            )
        except Exception as exc:  # noqa: BLE001 — best-effort, never block the booking
            logger.warning(
                "Make-up %s confirmed but obligation %s flip failed: %s",
                makeup.id,
                data.obligation_id,
                exc,
            )

    return makeup


@router.get("/bookable-slots", response_model=BookableSlotsResponse)
async def get_bookable_slots(
    coach_id: uuid.UUID = Query(..., description="Coach member id"),
    learner_id: uuid.UUID = Query(..., description="Learner member id"),
    from_date: date = Query(
        ..., alias="from", description="Window start (coach-local date)"
    ),
    to_date: date = Query(..., alias="to", description="Window end, inclusive"),
    _: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
) -> BookableSlotsResponse:
    """Return bookable make-up options for a coach + learner over [from, to].

    Admin-facing (the booker, per policy §3). Returns "open" slots (gaps in the
    coach's published availability) and "join_session" options (existing sessions
    with room — a make-up needn't be 1:1, policy §1). Spacing violations are
    *flagged*, not removed (decision D2). ``availability_set`` is False when the
    coach hasn't published a calendar — join options may still be returned.
    """
    if to_date < from_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="`from` must be on or before `to`.",
        )
    if (to_date - from_date).days > _MAX_WINDOW_DAYS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Window too large (max {_MAX_WINDOW_DAYS} days).",
        )

    return await _compute_bookable_slots_for(
        db,
        coach_id=coach_id,
        learner_id=learner_id,
        from_date=from_date,
        to_date=to_date,
    )


@router.post(
    "/bookings",
    response_model=MakeupBookingResponse,
    status_code=status.HTTP_201_CREATED,
)
async def confirm_makeup_booking(
    data: MakeupBookingCreate,
    _: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
) -> MakeupBookingResponse:
    """Confirm a make-up for a learner against a chosen session (admin; policy §3).

    The session is either a dedicated make-up session (pre-created) or an existing
    one the learner joins (policy §1). Enforces: a reason for reschedules (1b), one
    outstanding make-up at a time, one grace per block, the 14-day window, and
    session capacity. Writes a CONFIRMED MakeupBooking and books the learner in.
    """
    # Reason required for learner-initiated reschedules (policy §4 / 1b).
    _require_reschedule_reason(data.origin, data.reason)

    # The chosen session must exist and be led by this coach.
    session = (
        await db.execute(select(Session).where(Session.id == data.scheduled_session_id))
    ).scalar_one_or_none()
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Scheduled session not found."
        )
    coach_assigned = (
        await db.execute(
            select(SessionCoach.id).where(
                SessionCoach.session_id == session.id,
                SessionCoach.coach_id == data.coach_member_id,
            )
        )
    ).scalar_one_or_none()
    if coach_assigned is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Coach is not assigned to the chosen session.",
        )

    return await _confirm_makeup_against_session(db, data=data, session=session)


@router.post(
    "/open-slot",
    response_model=MakeupBookingResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_open_slot_makeup(
    data: MakeupOpenSlotCreate,
    _: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
) -> MakeupBookingResponse:
    """Create a dedicated make-up session in a coach's open slot and confirm the
    learner into it in one step (admin; design §4 Phase 2).

    Use this for a brand-new dedicated slot; to drop a learner into a session the
    coach already runs, use ``POST /makeups/bookings`` (policy §1). The new
    session is a COHORT_CLASS whose cohort is ``cohort_id`` or is derived from
    ``original_session_id``. Eligibility (one-outstanding, window, grace) is
    enforced by the shared core *after* the session is built; if it fails the
    request rolls back and no orphan session is left behind.
    """
    _require_reschedule_reason(data.origin, data.reason)

    starts_at = _as_utc(data.starts_at)
    ends_at = _as_utc(data.ends_at)
    if starts_at <= utc_now():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="The make-up slot must be in the future.",
        )

    # Fail fast before building any session rows (the shared core re-checks).
    await _assert_no_outstanding_makeup(db, data.learner_member_id)

    # Cohort for the new COHORT_CLASS session: explicit, else from the missed one.
    cohort_id = data.cohort_id
    if cohort_id is None and data.original_session_id is not None:
        row = (
            await db.execute(
                select(Session.cohort_id).where(Session.id == data.original_session_id)
            )
        ).one_or_none()
        if row is not None:
            cohort_id = row[0]
    if cohort_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Could not determine the cohort for the make-up session.",
        )

    # Refuse to create a slot overlapping a session the coach already runs — the
    # join-session path (POST /makeups/bookings) is for that case.
    overlap = (
        await db.execute(
            select(SessionCoach.id)
            .join(Session, Session.id == SessionCoach.session_id)
            .where(SessionCoach.coach_id == data.coach_member_id)
            .where(
                Session.status.in_([SessionStatus.SCHEDULED, SessionStatus.IN_PROGRESS])
            )
            .where(Session.starts_at < ends_at)
            .where(Session.ends_at > starts_at)
            .limit(1)
        )
    ).scalar_one_or_none()
    if overlap is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "The coach already has a session overlapping that time; "
                "use the join-session option instead."
            ),
        )

    # Build the dedicated make-up session + attach the coach (flush for the id;
    # the shared core commits everything atomically, or it all rolls back).
    session = Session(
        session_type=SessionType.COHORT_CLASS,
        cohort_id=cohort_id,
        title=(data.title or "Make-up session"),
        starts_at=starts_at,
        ends_at=ends_at,
        pool_id=data.pool_id,
        capacity=data.capacity,
        status=SessionStatus.SCHEDULED,
        published_at=utc_now(),
    )
    db.add(session)
    await db.flush()
    db.add(
        SessionCoach(
            session_id=session.id,
            coach_id=data.coach_member_id,
            role="lead",
        )
    )
    await db.flush()

    booking = MakeupBookingCreate(
        learner_member_id=data.learner_member_id,
        coach_member_id=data.coach_member_id,
        scheduled_session_id=session.id,
        origin=data.origin,
        reason=data.reason,
        original_session_id=data.original_session_id,
        block_kind=data.block_kind,
        block_id=data.block_id,
        obligation_id=data.obligation_id,
        used_grace=data.used_grace,
        spacing_overridden=data.spacing_overridden,
    )
    return await _confirm_makeup_against_session(db, data=booking, session=session)


@router.get("/bookings", response_model=list[MakeupBookingResponse])
async def list_makeup_bookings(
    learner_id: uuid.UUID | None = Query(None, description="Filter by learner"),
    coach_id: uuid.UUID | None = Query(None, description="Filter by coach"),
    status_filter: MakeupStatus | None = Query(
        None, alias="status", description="Filter by make-up status"
    ),
    _: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
) -> list[MakeupBookingResponse]:
    """List make-up bookings (admin), newest first; filter by learner/coach/status."""
    query = select(MakeupBooking)
    if learner_id is not None:
        query = query.where(MakeupBooking.learner_member_id == learner_id)
    if coach_id is not None:
        query = query.where(MakeupBooking.coach_member_id == coach_id)
    if status_filter is not None:
        query = query.where(MakeupBooking.status == status_filter)
    query = query.order_by(MakeupBooking.created_at.desc()).limit(200)
    rows = (await db.execute(query)).scalars().all()
    return list(rows)


@router.post("/bookings/{booking_id}/complete", response_model=MakeupBookingResponse)
async def complete_makeup_booking(
    booking_id: uuid.UUID,
    _: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
) -> MakeupBookingResponse:
    """Mark a make-up as delivered (admin). Sets COMPLETED + completed_at.

    The linked cohort payout obligation completes via payments' own
    attendance-driven flow — it is not flipped here, to avoid payout side effects.
    """
    makeup = (
        await db.execute(select(MakeupBooking).where(MakeupBooking.id == booking_id))
    ).scalar_one_or_none()
    if makeup is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Make-up not found."
        )
    if makeup.status not in (
        MakeupStatus.CONFIRMED,
        MakeupStatus.HELD,
        MakeupStatus.REQUESTED,
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Cannot complete a make-up in status {makeup.status.value}.",
        )
    makeup.status = MakeupStatus.COMPLETED
    makeup.completed_at = utc_now()
    await db.commit()
    await db.refresh(makeup)

    # Close the payout loop (best-effort): completing a make-up completes its
    # cohort obligation so the coach is paid for delivery.
    if makeup.obligation_id is not None:
        try:
            await complete_makeup_obligation(
                str(makeup.obligation_id), calling_service="sessions"
            )
        except Exception as exc:  # noqa: BLE001 — best-effort, never block the response
            logger.warning(
                "Make-up %s completed but obligation %s completion failed: %s",
                makeup.id,
                makeup.obligation_id,
                exc,
            )

    return makeup


@router.post("/bookings/{booking_id}/cancel", response_model=MakeupBookingResponse)
async def cancel_makeup_booking(
    booking_id: uuid.UUID,
    _: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
) -> MakeupBookingResponse:
    """Cancel a make-up (admin). Sets CANCELLED; terminal states are rejected."""
    makeup = (
        await db.execute(select(MakeupBooking).where(MakeupBooking.id == booking_id))
    ).scalar_one_or_none()
    if makeup is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Make-up not found."
        )
    if makeup.status in (
        MakeupStatus.COMPLETED,
        MakeupStatus.CANCELLED,
        MakeupStatus.EXPIRED,
        MakeupStatus.FORFEITED,
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Cannot cancel a make-up in status {makeup.status.value}.",
        )
    makeup.status = MakeupStatus.CANCELLED
    await db.commit()
    await db.refresh(makeup)
    return makeup


# ---------------------------------------------------------------------------
# Learner self-serve (Phase 1.5)
# ---------------------------------------------------------------------------


@router.get("/me/options", response_model=BookableSlotsResponse)
async def get_my_bookable_options(
    coach_id: uuid.UUID = Query(..., description="Coach member id"),
    from_date: date = Query(..., alias="from"),
    to_date: date = Query(..., alias="to"),
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> BookableSlotsResponse:
    """A learner's own bookable make-up options for a coach over [from, to]."""
    if to_date < from_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="`from` must be on or before `to`.",
        )
    if (to_date - from_date).days > _MAX_WINDOW_DAYS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Window too large (max {_MAX_WINDOW_DAYS} days).",
        )
    learner_id = await _resolve_member_id(current_user.user_id)
    return await _compute_bookable_slots_for(
        db,
        coach_id=coach_id,
        learner_id=learner_id,
        from_date=from_date,
        to_date=to_date,
    )


@router.post(
    "/me/requests",
    response_model=MakeupBookingResponse,
    status_code=status.HTTP_201_CREATED,
)
async def request_makeup(
    data: MakeupRequestCreate,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> MakeupBookingResponse:
    """A learner requests a make-up against a chosen session (REQUESTED + soft hold).

    Admin one-tap confirms it later (booking the learner in + the obligation flip
    happen then). Light gates: a reason for reschedules (1b), one outstanding
    make-up at a time, and the session must exist, be led by the coach, and have
    room.
    """
    learner_id = await _resolve_member_id(current_user.user_id)

    _require_reschedule_reason(data.origin, data.reason)

    open_count = (
        await db.execute(
            select(func.count())
            .select_from(MakeupBooking)
            .where(
                MakeupBooking.learner_member_id == learner_id,
                MakeupBooking.status.in_(
                    [MakeupStatus.REQUESTED, MakeupStatus.HELD, MakeupStatus.CONFIRMED]
                ),
            )
        )
    ).scalar_one()
    if open_count:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You already have an open make-up request.",
        )

    session = (
        await db.execute(select(Session).where(Session.id == data.scheduled_session_id))
    ).scalar_one_or_none()
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Session not found."
        )
    coach_assigned = (
        await db.execute(
            select(SessionCoach.id).where(
                SessionCoach.session_id == session.id,
                SessionCoach.coach_id == data.coach_member_id,
            )
        )
    ).scalar_one_or_none()
    if coach_assigned is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="That session isn't led by this coach.",
        )
    confirmed_count = (
        await db.execute(
            select(func.count())
            .select_from(SessionBooking)
            .where(
                SessionBooking.session_id == session.id,
                SessionBooking.status == SessionBookingStatus.CONFIRMED,
            )
        )
    ).scalar_one()
    if confirmed_count >= session.capacity:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="That session is full."
        )

    makeup = MakeupBooking(
        learner_member_id=learner_id,
        coach_member_id=data.coach_member_id,
        learner_type=MakeupLearnerType.COHORT,
        origin=data.origin,
        original_session_id=data.original_session_id,
        scheduled_session_id=session.id,
        status=MakeupStatus.REQUESTED,
        hold_expires_at=utc_now() + timedelta(minutes=_HOLD_MINUTES),
        notes=data.reason,
    )
    db.add(makeup)
    await db.commit()
    await db.refresh(makeup)
    return makeup


@router.get("/me/requests", response_model=list[MakeupBookingResponse])
async def list_my_makeups(
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> list[MakeupBookingResponse]:
    """The authenticated learner's make-up bookings (newest first)."""
    learner_id = await _resolve_member_id(current_user.user_id)
    rows = (
        (
            await db.execute(
                select(MakeupBooking)
                .where(MakeupBooking.learner_member_id == learner_id)
                .order_by(MakeupBooking.created_at.desc())
                .limit(100)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


@router.post("/bookings/{booking_id}/confirm", response_model=MakeupBookingResponse)
async def confirm_makeup_request(
    booking_id: uuid.UUID,
    _: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
) -> MakeupBookingResponse:
    """Admin one-tap confirm of a learner's REQUESTED/HELD make-up.

    Books the learner into the session (capacity-checked) and flips the linked
    obligation, mirroring the direct-confirm tail.
    """
    makeup = (
        await db.execute(select(MakeupBooking).where(MakeupBooking.id == booking_id))
    ).scalar_one_or_none()
    if makeup is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Make-up not found."
        )
    if makeup.status not in (MakeupStatus.REQUESTED, MakeupStatus.HELD):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Cannot confirm a make-up in status {makeup.status.value}.",
        )
    if makeup.scheduled_session_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Make-up has no scheduled session.",
        )

    learner = await get_member_by_id(
        str(makeup.learner_member_id), calling_service="sessions"
    )
    if not learner or not learner.get("auth_id"):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Learner not found."
        )

    await _book_learner_into_session(
        db,
        session_id=makeup.scheduled_session_id,
        learner_member_id=makeup.learner_member_id,
        learner_auth_id=learner["auth_id"],
    )

    makeup.status = MakeupStatus.CONFIRMED
    makeup.hold_expires_at = None
    await db.commit()
    await db.refresh(makeup)

    if makeup.obligation_id is not None:
        try:
            await schedule_makeup_obligation(
                str(makeup.obligation_id),
                str(makeup.scheduled_session_id),
                calling_service="sessions",
                notes=makeup.notes,
            )
        except Exception as exc:  # noqa: BLE001 — best-effort
            logger.warning(
                "Confirmed make-up %s but obligation %s flip failed: %s",
                makeup.id,
                makeup.obligation_id,
                exc,
            )

    return makeup
