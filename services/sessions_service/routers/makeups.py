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
from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from libs.common.service_client import (
    complete_makeup_obligation,
    get_coach_availability,
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
)
from services.sessions_service.schemas import (
    BookableSlotResponse,
    BookableSlotsResponse,
    MakeupBookingCreate,
    MakeupBookingResponse,
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

    avail = await get_coach_availability(str(coach_id), calling_service="sessions")
    calendar = (avail or {}).get("availability_calendar") or {}
    availability_set = bool(calendar)
    min_hours = (avail or {}).get("min_hours_between_sessions")

    # Window bounds in UTC. Dates are coach-local; ±1 day padding tolerates the
    # local/UTC offset at the window edges.
    lo = datetime.combine(from_date - timedelta(days=1), time.min, tzinfo=timezone.utc)
    hi = datetime.combine(to_date + timedelta(days=2), time.min, tzinfo=timezone.utc)

    # Coach's scheduled sessions in-window. Each occupies the coach's time (blocks
    # "open" slots); those with room ALSO surface as "join_session" options
    # (a make-up needn't be 1:1 — policy §1).
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

    # Confirmed-booking counts per session → remaining capacity.
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

    # Learner's other sessions (confirmed bookings) → spacing reference points.
    # Phase 0: SessionBooking-derived only; cohort-enrollment-derived sessions
    # fold in with block resolution (Phase 1).
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
    if (
        data.origin == MakeupOrigin.LEARNER_RESCHEDULE
        and not (data.reason or "").strip()
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="A reschedule needs a reason.",
        )

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

    # One outstanding make-up at a time (policy §4).
    open_count = (
        await db.execute(
            select(func.count())
            .select_from(MakeupBooking)
            .where(
                MakeupBooking.learner_member_id == data.learner_member_id,
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
    existing = (
        await db.execute(
            select(SessionBooking).where(
                SessionBooking.session_id == session.id,
                SessionBooking.member_id == data.learner_member_id,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
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
                status_code=status.HTTP_409_CONFLICT,
                detail="The chosen session is full.",
            )
        db.add(
            SessionBooking(
                session_id=session.id,
                member_id=data.learner_member_id,
                member_auth_id=learner["auth_id"],
                status=SessionBookingStatus.CONFIRMED,
                channel=BookingChannel.ADMIN,
                booked_at=utc_now(),
                confirmed_at=utc_now(),
            )
        )
    elif existing.status != SessionBookingStatus.CONFIRMED:
        existing.status = SessionBookingStatus.CONFIRMED
        existing.confirmed_at = utc_now()
        existing.expires_at = None

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
