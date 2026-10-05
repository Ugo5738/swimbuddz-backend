"""Internal service-to-service endpoints for sessions-service.

These endpoints are authenticated with service_role JWT only.
They are NOT exposed through the gateway — only other backend services
call them directly via Docker network.
"""

import uuid
from datetime import datetime, timedelta
from typing import List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_service_role
from libs.auth.models import AuthUser
from libs.common.config import get_settings
from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from libs.common.service_client import (
    cancel_opportunities_for_context,
    dispatch_notification,
    get_member_by_auth_id,
    internal_get,
    internal_post,
    reconcile_session_ride_schedule,
    reconcile_volunteer_session_schedule,
)
from libs.common.session_access import denial_message
from libs.db.session import get_async_db
from services.sessions_service.models import (
    BookingChannel,
    GuestPass,
    Session,
    SessionBooking,
    SessionBookingStatus,
    SessionCoach,
    SessionParticipant,
    SessionStatus,
    SessionType,
)
from services.sessions_service.schemas import (
    BookingConfirmRequest,
    BulkBookingRequest,
    BulkBookingResponse,
    BundleBookingConfirmRequest,
    BundleBookingConfirmResponse,
    BundleBookingLineResponse,
    BundleBookingReleaseRequest,
    BundleBookingReleaseResponse,
    BundleBookingReserveRequest,
    BundleBookingReserveResponse,
    MemberSessionAccessResponse,
    SessionBookingResponse,
    WalkInAttendanceReconcileRequest,
)
from services.sessions_service.services.booking_attendance import (
    sync_booking_attendance,
)
from services.sessions_service.services.booking_capacity import (
    PENDING_TTL_MINUTES,
    assert_booking_capacity,
)
from services.sessions_service.services.booking_confirmation import (
    allocate_total,
    deliver_confirmation,
    queue_confirmation,
)
from services.sessions_service.services.commercial import (
    apply_session_rate,
    sync_legacy_session_rates,
)
from services.sessions_service.services.pricing import (
    normalize_pricing_payload,
    pricing_payload_from_session,
)
from services.sessions_service.services.session_access import (
    evaluate_session_access_for_member,
    get_member_session_access_payload,
)

router = APIRouter(prefix="/internal/sessions", tags=["internal"])
logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class SessionBasic(BaseModel):
    id: str
    title: str
    description: Optional[str] = None
    notes: Optional[str] = None
    session_type: str
    status: str
    starts_at: str
    ends_at: str
    pool_id: Optional[str] = None
    location_name: Optional[str] = None
    location_address: Optional[str] = None
    location: Optional[str] = None
    cohort_id: Optional[str] = None
    club_id: Optional[str] = None
    pod_id: Optional[str] = None
    capacity: int
    # pool_fee is returned in KOBO (integer) for service-to-service use.
    # Wallet-only consumers require pool_fee to be exactly divisible by one Bubble.
    pool_fee: Optional[int] = None
    cohort_fee_mode: str = "included"
    guest_fee_kobo: Optional[int] = None
    community_dropin_fee_kobo: Optional[int] = None
    visiting_club_fee_kobo: Optional[int] = None
    allows_community_dropins: bool = False
    allows_visiting_club_members: bool = False
    ride_share_fee: Optional[int] = None
    occupied_slots: int = 0
    confirmed_booking_member_ids: List[str] = Field(default_factory=list)
    coach_member_ids: List[str] = Field(default_factory=list)
    week_number: Optional[int] = None
    lesson_title: Optional[str] = None
    timezone: str = "Africa/Lagos"


class MemberSessionCommitment(BaseModel):
    """A confirmed member commitment joined to its scheduled session."""

    booking_id: str
    session_id: str
    member_id: str
    member_auth_id: str
    title: str
    session_type: str
    session_status: str
    starts_at: str
    ends_at: str
    location_name: Optional[str] = None
    cohort_id: Optional[str] = None
    club_id: Optional[str] = None
    pod_id: Optional[str] = None
    event_id: Optional[str] = None
    week_number: Optional[int] = None


class NextSessionResponse(BaseModel):
    starts_at: str
    title: str
    location_name: Optional[str] = None


class GenerateCohortSessionsRequest(BaseModel):
    # Half-open window (from_date, to_date]. Typically from_date = the cohort's
    # pre-extension end_date and to_date = the new (post-extension) end_date.
    from_date: datetime
    to_date: datetime


class GenerateCohortSessionsResponse(BaseModel):
    created: int
    skipped: int
    week_numbers: List[int]
    reason: Optional[str] = None


class SessionSummaryBatchRequest(BaseModel):
    session_ids: List[uuid.UUID] = Field(default_factory=list, max_length=200)


class SessionListSummary(BaseModel):
    id: str
    title: str
    session_type: str
    starts_at: str
    location_name: Optional[str] = None
    location: Optional[str] = None
    club_id: Optional[str] = None
    pod_id: Optional[str] = None


class EventSessionLink(BaseModel):
    id: uuid.UUID
    status: str


class EventSessionLinks(BaseModel):
    event_id: uuid.UUID
    linked_count: int
    active_count: int
    sessions: list[EventSessionLink]


class EventSessionSync(BaseModel):
    title: str | None = None
    description: str | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    timezone: str | None = None
    pool_id: uuid.UUID | None = None
    location_name: str | None = None
    capacity: int | None = Field(default=None, ge=1)
    cancel: bool = False
    cancellation_reason: str | None = None


class EventSessionSyncResult(BaseModel):
    event_id: uuid.UUID
    linked_count: int
    updated_count: int
    cancelled_count: int


class EventSessionLinksBatchRequest(BaseModel):
    event_ids: list[uuid.UUID] = Field(default_factory=list, max_length=200)


class SessionParticipantSettlementContext(BaseModel):
    participant_id: uuid.UUID
    session_id: uuid.UUID
    session_type: str
    source: str
    participant_kind: str
    full_name: str
    email: str | None = None
    phone: str | None = None
    fee_amount_kobo: int
    payment_status: str
    payment_method: str | None = None
    payment_reference: str | None = None


class SessionParticipantPaymentConfirm(BaseModel):
    payment_reference: str = Field(min_length=1, max_length=128)
    payment_method: str = Field(min_length=1, max_length=32)
    amount_kobo: int = Field(gt=0)
    paid_at: datetime


@router.get(
    "/participants/{participant_id}/settlement-context",
    response_model=SessionParticipantSettlementContext,
)
async def get_participant_settlement_context(
    participant_id: uuid.UUID,
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    participant = (
        await db.execute(
            select(SessionParticipant).where(SessionParticipant.id == participant_id)
        )
    ).scalar_one_or_none()
    if participant is None:
        raise HTTPException(status_code=404, detail="Session participant not found")
    session = await db.get(Session, participant.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return SessionParticipantSettlementContext(
        participant_id=participant.id,
        session_id=session.id,
        session_type=(
            session.session_type.value
            if hasattr(session.session_type, "value")
            else str(session.session_type)
        ),
        source=participant.source,
        participant_kind=participant.participant_kind,
        full_name=participant.full_name_snapshot,
        email=participant.email_snapshot,
        phone=participant.phone_snapshot,
        fee_amount_kobo=int(participant.fee_amount_kobo or 0),
        payment_status=participant.payment_status,
        payment_method=participant.payment_method,
        payment_reference=participant.payment_reference,
    )


@router.post(
    "/participants/{participant_id}/confirm-payment",
    response_model=SessionParticipantSettlementContext,
)
async def confirm_participant_payment(
    participant_id: uuid.UUID,
    payload: SessionParticipantPaymentConfirm,
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    participant = (
        await db.execute(
            select(SessionParticipant)
            .where(SessionParticipant.id == participant_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if participant is None:
        raise HTTPException(status_code=404, detail="Session participant not found")
    if participant.source != "walk_in" or participant.participant_kind != "guest":
        raise HTTPException(
            status_code=409,
            detail="Only unregistered guest walk-ins use participant settlement",
        )
    expected_kobo = int(participant.fee_amount_kobo or 0)
    if payload.amount_kobo != expected_kobo:
        raise HTTPException(
            status_code=409,
            detail=(
                "Payment amount does not match the frozen walk-in fee "
                f"({expected_kobo} kobo)"
            ),
        )
    if participant.payment_status == "paid":
        if participant.payment_reference == payload.payment_reference:
            session = await db.get(Session, participant.session_id)
            return SessionParticipantSettlementContext(
                participant_id=participant.id,
                session_id=participant.session_id,
                session_type=(
                    session.session_type.value
                    if session and hasattr(session.session_type, "value")
                    else str(session.session_type)
                    if session
                    else "unknown"
                ),
                source=participant.source,
                participant_kind=participant.participant_kind,
                full_name=participant.full_name_snapshot,
                email=participant.email_snapshot,
                phone=participant.phone_snapshot,
                fee_amount_kobo=expected_kobo,
                payment_status=participant.payment_status,
                payment_method=participant.payment_method,
                payment_reference=participant.payment_reference,
            )
        raise HTTPException(
            status_code=409,
            detail="This walk-in already has a different settled payment",
        )
    if participant.payment_status in {"waived", "included"}:
        raise HTTPException(
            status_code=409,
            detail=f"This walk-in is already closed as {participant.payment_status}",
        )

    participant.payment_status = "paid"
    participant.payment_method = payload.payment_method
    participant.payment_reference = payload.payment_reference
    participant.paid_at = payload.paid_at
    await db.commit()
    await db.refresh(participant)
    session = await db.get(Session, participant.session_id)
    return SessionParticipantSettlementContext(
        participant_id=participant.id,
        session_id=participant.session_id,
        session_type=(
            session.session_type.value
            if session and hasattr(session.session_type, "value")
            else str(session.session_type)
            if session
            else "unknown"
        ),
        source=participant.source,
        participant_kind=participant.participant_kind,
        full_name=participant.full_name_snapshot,
        email=participant.email_snapshot,
        phone=participant.phone_snapshot,
        fee_amount_kobo=expected_kobo,
        payment_status=participant.payment_status,
        payment_method=participant.payment_method,
        payment_reference=participant.payment_reference,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

# NOTE: Static path "/scheduled" must be registered before the
# parameterized "/{session_id}" to avoid route collision (FastAPI
# matches routes in definition order).


@router.get("/scheduled", response_model=List[SessionBasic])
async def get_scheduled_sessions(
    # datetime, not str: binds as timestamptz (str 500s the starts_at comparison)
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    include_completed: bool = False,
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Get scheduled sessions within a date range."""
    statuses = [SessionStatus.SCHEDULED]
    if include_completed:
        statuses.extend([SessionStatus.IN_PROGRESS, SessionStatus.COMPLETED])
    query = select(Session).where(Session.status.in_(statuses))
    if start_date:
        query = query.where(Session.starts_at >= start_date)
    if end_date:
        query = query.where(Session.starts_at < end_date)
    query = query.order_by(Session.starts_at.asc())
    result = await db.execute(query)
    sessions = result.scalars().all()
    session_ids = [session.id for session in sessions]
    confirmed_by_session: dict[uuid.UUID, list[str]] = {
        session_id: [] for session_id in session_ids
    }
    occupied_by_session: dict[uuid.UUID, int] = {
        session_id: 0 for session_id in session_ids
    }
    coaches_by_session: dict[uuid.UUID, list[str]] = {
        session_id: [] for session_id in session_ids
    }
    if session_ids:
        booking_rows = (
            await db.execute(
                select(
                    SessionBooking.session_id,
                    SessionBooking.member_id,
                    SessionBooking.party_size,
                ).where(
                    SessionBooking.session_id.in_(session_ids),
                    SessionBooking.status == SessionBookingStatus.CONFIRMED,
                )
            )
        ).all()
        for session_id, member_id, party_size in booking_rows:
            confirmed_by_session[session_id].append(str(member_id))
            occupied_by_session[session_id] += int(party_size or 1)

        coach_rows = (
            await db.execute(
                select(SessionCoach.session_id, SessionCoach.coach_id).where(
                    SessionCoach.session_id.in_(session_ids)
                )
            )
        ).all()
        for session_id, coach_id in coach_rows:
            coaches_by_session[session_id].append(str(coach_id))

    return [
        SessionBasic(
            id=str(s.id),
            title=s.title,
            description=s.description,
            notes=s.notes,
            session_type=s.session_type.value,
            status=s.status.value,
            starts_at=s.starts_at.isoformat(),
            ends_at=s.ends_at.isoformat(),
            pool_id=str(s.pool_id) if s.pool_id else None,
            location_name=s.location_name,
            location_address=s.location_address,
            location=s.location.value if s.location else None,
            cohort_id=str(s.cohort_id) if s.cohort_id else None,
            club_id=(
                str(club_id) if (club_id := getattr(s, "club_id", None)) else None
            ),
            pod_id=str(s.pod_id) if s.pod_id else None,
            capacity=s.capacity,
            pool_fee=s.pool_fee,
            cohort_fee_mode=getattr(s, "cohort_fee_mode", None) or "included",
            ride_share_fee=s.ride_share_fee,
            occupied_slots=occupied_by_session[s.id],
            confirmed_booking_member_ids=confirmed_by_session[s.id],
            coach_member_ids=coaches_by_session[s.id],
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
    guest_swimmer_hours: float = 0.0
    exact_guest_swimmer_hours: float = 0.0
    estimated_guest_swimmer_hours: float = 0.0
    total_attendance_records: int = 0
    member_attendance_records: int = 0
    guest_attendance_records: int = 0
    guest_pass_attendance_records: int = 0
    booking_guest_attendance_records: int = 0
    walk_in_guest_attendance_records: int = 0
    attendance_available: bool = False
    by_type: dict | None = None
    most_active_location: str | None = None
    busiest_session_title: str | None = None
    busiest_session_attendance: int = 0
    most_popular_day: str | None = None
    most_popular_time_slot: str | None = None
    session_details: list[dict] | None = None


class CampaignBookingStats(BaseModel):
    campaign_key: str
    total: int = 0
    pending: int = 0
    confirmed: int = 0
    cancelled: int = 0
    expired: int = 0


@router.get("/bookings/campaign-stats", response_model=CampaignBookingStats)
async def get_campaign_booking_stats(
    campaign_key: str = Query(..., min_length=1, max_length=80),
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
) -> CampaignBookingStats:
    """Count booking outcomes attributed to a digest or other campaign."""
    rows = (
        await db.execute(
            select(SessionBooking.status, func.count(SessionBooking.id))
            .where(SessionBooking.campaign_key == campaign_key)
            .group_by(SessionBooking.status)
        )
    ).all()
    counts = {
        status.value if hasattr(status, "value") else str(status): int(count)
        for status, count in rows
    }
    return CampaignBookingStats(
        campaign_key=campaign_key,
        total=sum(counts.values()),
        pending=counts.get("pending", 0),
        confirmed=counts.get("confirmed", 0),
        cancelled=counts.get("cancelled", 0),
        expired=counts.get("expired", 0),
    )


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

    # GuestPass attendance carries exact swim minutes. Keep converted guests
    # out of the guest bucket because their historical hours move into the
    # linked member's report.
    guest_pass_rows = (
        await db.execute(
            select(
                GuestPass.session_id,
                GuestPass.actual_swim_minutes,
                GuestPass.converted_member_id,
            )
            .join(Session, Session.id == GuestPass.session_id)
            .where(
                Session.starts_at >= parsed_from,
                Session.starts_at <= parsed_to,
                GuestPass.status == "attended",
            )
        )
    ).all()
    # A later member conversion does not change the historical fact that this
    # person attended as a guest. It *does* move their swimmer-hours into the
    # member report, so exclude converted rows only from the guest-hours bucket.
    exact_guest_minutes = sum(
        int(minutes or 0)
        for _, minutes, converted_member_id in guest_pass_rows
        if converted_member_id is None
    )
    guest_pass_counts = Counter(str(session_id) for session_id, _, _ in guest_pass_rows)

    if not sessions:
        return SessionDetailedStats(
            guest_swimmer_hours=round(exact_guest_minutes / 60, 1),
            exact_guest_swimmer_hours=round(exact_guest_minutes / 60, 1),
            guest_pass_attendance_records=len(guest_pass_rows),
            guest_attendance_records=len(guest_pass_rows),
            total_attendance_records=len(guest_pass_rows),
        )

    # Attendance service owns member, attached-booking-guest and canonical
    # participant attendance. Ask it for all actual PRESENT/LATE humans in
    # these sessions rather than inferring attendance from bookings.
    attendance_counts: dict[str, dict] = {}
    attendance_available = False
    try:
        attendance_response = await internal_get(
            service_url=get_settings().ATTENDANCE_SERVICE_URL,
            path="/internal/attendance/session-counts",
            calling_service="sessions",
            params={"ids": ",".join(str(session.id) for session in sessions)},
            timeout=20.0,
        )
        if attendance_response.status_code == 200:
            attendance_available = True
            attendance_counts = {
                str(row["session_id"]): row for row in attendance_response.json()
            }
    except httpx.HTTPError:
        attendance_counts = {}

    participant_ids = {
        uuid.UUID(str(participant_id))
        for row in attendance_counts.values()
        for participant_id in (row.get("participant_ids") or [])
    }
    guest_participant_ids: set[uuid.UUID] = set()
    if participant_ids:
        guest_participant_ids = set(
            (
                await db.execute(
                    select(SessionParticipant.id).where(
                        SessionParticipant.id.in_(participant_ids),
                        SessionParticipant.participant_kind == "guest",
                    )
                )
            )
            .scalars()
            .all()
        )

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

    # Fold actual attendance into delivery metrics. Booking guests and door
    # walk-ins currently do not store exact swim minutes, so their swimmer-hours
    # are estimated from the full scheduled session duration. Do not subtract an
    # arbitrary hour: that previously erased one-hour Academy sessions entirely.
    details: list[dict] = []
    member_attendance_records = 0
    booking_guest_attendance_records = 0
    walk_in_guest_attendance_records = 0
    estimated_guest_hours = 0.0
    busiest_session_title = None
    busiest_session_attendance = 0
    attended_location_counts: Counter = Counter()
    attended_day_counts: Counter = Counter()
    attended_slot_counts: Counter = Counter()

    for session in sessions:
        session_id = str(session.id)
        duration_hours = (session.ends_at - session.starts_at).total_seconds() / 3600
        effective_guest_hours = max(0.0, duration_hours)
        attendance = attendance_counts.get(session_id) or {}
        member_count = int(attendance.get("member_attended") or 0)
        booking_guest_count = int(attendance.get("booking_guest_attended") or 0)
        participant_guest_count = sum(
            1
            for participant_id in (attendance.get("participant_ids") or [])
            if uuid.UUID(str(participant_id)) in guest_participant_ids
        )
        pass_count = int(guest_pass_counts.get(session_id, 0))
        actual_attendance = int(attendance.get("attended") or 0) + pass_count

        member_attendance_records += member_count
        booking_guest_attendance_records += booking_guest_count
        walk_in_guest_attendance_records += participant_guest_count
        estimated_guest_hours += (
            booking_guest_count + participant_guest_count
        ) * effective_guest_hours

        if actual_attendance > busiest_session_attendance:
            busiest_session_attendance = actual_attendance
            busiest_session_title = session.title
        if actual_attendance:
            if session.location_name:
                attended_location_counts[session.location_name] += actual_attendance
            attended_day_counts[DAYS[session.starts_at.weekday()]] += actual_attendance
            attended_slot_counts[time_slot(session.starts_at.hour)] += actual_attendance

        details.append(
            {
                "id": session_id,
                "title": session.title,
                "hours": round(duration_hours, 2),
                "location": session.location_name,
                "type": session.session_type.value
                if hasattr(session.session_type, "value")
                else str(session.session_type),
                "capacity": session.capacity,
                "attendance": actual_attendance,
                "member_attendance": member_count,
                "guest_attendance": (
                    booking_guest_count + participant_guest_count + pass_count
                ),
            }
        )

    exact_guest_hours = exact_guest_minutes / 60
    guest_attendance_records = (
        booking_guest_attendance_records
        + walk_in_guest_attendance_records
        + len(guest_pass_rows)
    )
    total_attendance_records = member_attendance_records + guest_attendance_records

    # Prefer attendance-weighted popularity when attendance data is available;
    # retain scheduled-session frequency as a graceful legacy fallback.
    if attended_location_counts:
        most_active = attended_location_counts.most_common(1)[0][0]
    if attended_day_counts:
        most_popular_day = attended_day_counts.most_common(1)[0][0]
    if attended_slot_counts:
        most_popular_slot = attended_slot_counts.most_common(1)[0][0]

    return SessionDetailedStats(
        total_sessions=len(sessions),
        total_pool_hours=round(total_hours, 1),
        guest_swimmer_hours=round(exact_guest_hours + estimated_guest_hours, 1),
        exact_guest_swimmer_hours=round(exact_guest_hours, 1),
        estimated_guest_swimmer_hours=round(estimated_guest_hours, 1),
        total_attendance_records=total_attendance_records,
        member_attendance_records=member_attendance_records,
        guest_attendance_records=guest_attendance_records,
        guest_pass_attendance_records=len(guest_pass_rows),
        booking_guest_attendance_records=booking_guest_attendance_records,
        walk_in_guest_attendance_records=walk_in_guest_attendance_records,
        attendance_available=attendance_available,
        by_type=dict(type_counts) if type_counts else None,
        most_active_location=most_active,
        busiest_session_title=busiest_session_title,
        busiest_session_attendance=busiest_session_attendance,
        most_popular_day=most_popular_day,
        most_popular_time_slot=most_popular_slot,
        session_details=details,
    )


class ConvertedGuestHours(BaseModel):
    member_id: uuid.UUID
    swimmer_hours: float = 0.0


@router.get(
    "/member/{member_id}/converted-guest-hours",
    response_model=ConvertedGuestHours,
)
async def get_converted_guest_hours(
    member_id: uuid.UUID,
    date_from: datetime = Query(..., alias="from"),
    date_to: datetime = Query(..., alias="to"),
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
) -> ConvertedGuestHours:
    """Return standalone guest swim history linked to a converted member."""
    minutes = int(
        (
            await db.execute(
                select(func.coalesce(func.sum(GuestPass.actual_swim_minutes), 0))
                .join(Session, Session.id == GuestPass.session_id)
                .where(
                    GuestPass.converted_member_id == member_id,
                    GuestPass.status == "attended",
                    Session.starts_at >= date_from,
                    Session.starts_at <= date_to,
                )
            )
        ).scalar_one()
        or 0
    )
    return ConvertedGuestHours(
        member_id=member_id,
        swimmer_hours=round(minutes / 60, 1),
    )


@router.get(
    "/member/{member_auth_id}/session-commitments",
    response_model=List[MemberSessionCommitment],
)
async def list_member_session_commitments(
    member_auth_id: str,
    date_from: datetime = Query(..., alias="from"),
    date_to: datetime = Query(..., alias="to"),
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Confirmed, dated session commitments for member reporting.

    This uses the session's scheduled time as the reporting window, not the
    booking creation time. It lets reporting distinguish "expected to attend"
    from attendance records that happen to exist.
    """
    result = await db.execute(
        select(SessionBooking, Session)
        .join(Session, Session.id == SessionBooking.session_id)
        .where(
            SessionBooking.member_auth_id == member_auth_id,
            SessionBooking.status == SessionBookingStatus.CONFIRMED,
            Session.starts_at >= date_from,
            Session.starts_at <= date_to,
            Session.status.in_(
                [
                    SessionStatus.SCHEDULED,
                    SessionStatus.IN_PROGRESS,
                    SessionStatus.COMPLETED,
                ]
            ),
        )
        .order_by(Session.starts_at.asc())
    )

    return [
        MemberSessionCommitment(
            booking_id=str(booking.id),
            session_id=str(session.id),
            member_id=str(booking.member_id),
            member_auth_id=booking.member_auth_id,
            title=session.title,
            session_type=session.session_type.value,
            session_status=session.status.value,
            starts_at=session.starts_at.isoformat(),
            ends_at=session.ends_at.isoformat(),
            location_name=session.location_name,
            cohort_id=str(session.cohort_id) if session.cohort_id else None,
            club_id=(
                str(club_id) if (club_id := getattr(session, "club_id", None)) else None
            ),
            pod_id=str(session.pod_id) if session.pod_id else None,
            event_id=str(session.event_id) if session.event_id else None,
            week_number=session.week_number,
        )
        for booking, session in result.all()
    ]


@router.post("/summaries/batch", response_model=List[SessionListSummary])
async def get_session_summaries_batch(
    payload: SessionSummaryBatchRequest,
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
) -> List[SessionListSummary]:
    """Return display summaries for many session IDs in one database query."""
    session_ids = list(dict.fromkeys(payload.session_ids))
    if not session_ids:
        return []

    sessions = (
        (await db.execute(select(Session).where(Session.id.in_(session_ids))))
        .scalars()
        .all()
    )
    by_id = {session.id: session for session in sessions}
    return [
        SessionListSummary(
            id=str(session.id),
            title=session.title,
            session_type=session.session_type.value,
            starts_at=session.starts_at.isoformat(),
            location_name=session.location_name,
            location=session.location.value if session.location else None,
            club_id=(
                str(club_id) if (club_id := getattr(session, "club_id", None)) else None
            ),
            pod_id=str(session.pod_id) if session.pod_id else None,
        )
        for session_id in session_ids
        if (session := by_id.get(session_id)) is not None
    ]


@router.get("/events/{event_id}/links", response_model=EventSessionLinks)
async def get_event_session_links(
    event_id: uuid.UUID,
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    sessions = list(
        (
            await db.execute(
                select(Session)
                .where(
                    Session.session_type == SessionType.EVENT,
                    Session.event_id == event_id,
                )
                .order_by(Session.starts_at, Session.id)
            )
        ).scalars()
    )
    active = [item for item in sessions if item.status != SessionStatus.CANCELLED]
    return EventSessionLinks(
        event_id=event_id,
        linked_count=len(sessions),
        active_count=len(active),
        sessions=[
            EventSessionLink(id=item.id, status=item.status.value) for item in sessions
        ],
    )


@router.post("/events/links/batch", response_model=dict[str, EventSessionLinks])
async def get_event_session_links_batch(
    payload: EventSessionLinksBatchRequest,
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    event_ids = list(dict.fromkeys(payload.event_ids))
    if not event_ids:
        return {}
    sessions = list(
        (
            await db.execute(
                select(Session)
                .where(
                    Session.session_type == SessionType.EVENT,
                    Session.event_id.in_(event_ids),
                )
                .order_by(Session.starts_at, Session.id)
            )
        ).scalars()
    )
    grouped: dict[uuid.UUID, list[Session]] = {event_id: [] for event_id in event_ids}
    for session in sessions:
        if session.event_id is not None:
            grouped.setdefault(session.event_id, []).append(session)
    return {
        str(event_id): EventSessionLinks(
            event_id=event_id,
            linked_count=len(items),
            active_count=sum(item.status != SessionStatus.CANCELLED for item in items),
            sessions=[
                EventSessionLink(id=item.id, status=item.status.value) for item in items
            ],
        )
        for event_id, items in grouped.items()
    }


@router.patch("/events/{event_id}/sync", response_model=EventSessionSyncResult)
async def sync_event_sessions(
    event_id: uuid.UUID,
    payload: EventSessionSync,
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Apply Event-owned shared fields atomically to linked Sessions.

    Historical/in-progress Sessions cannot be silently rewritten. Event
    cancellation is different: every non-completed linked Session is cancelled
    while completed history remains intact.
    """
    sessions = list(
        (
            await db.execute(
                select(Session)
                .where(
                    Session.session_type == SessionType.EVENT,
                    Session.event_id == event_id,
                )
                .with_for_update()
            )
        ).scalars()
    )
    values = payload.model_dump(
        exclude_unset=True,
        exclude={"cancel", "cancellation_reason"},
    )
    required_shared = {"title", "starts_at", "ends_at", "timezone", "capacity"}
    cleared_required = [
        field for field in required_shared if field in values and values[field] is None
    ]
    if cleared_required:
        raise HTTPException(
            status_code=422,
            detail=(
                "Linked Event Sessions cannot clear required shared fields: "
                + ", ".join(sorted(cleared_required))
            ),
        )
    editable = {SessionStatus.DRAFT, SessionStatus.SCHEDULED}
    if values:
        blocked = [
            item
            for item in sessions
            if item.status not in editable and item.status != SessionStatus.CANCELLED
        ]
        if blocked:
            raise HTTPException(
                status_code=409,
                detail=(
                    "The Event has an in-progress or completed Session. Its shared "
                    "schedule fields can no longer be changed."
                ),
            )

    if "capacity" in values:
        editable_ids = [item.id for item in sessions if item.status in editable]
        occupied_by_session: dict[uuid.UUID, int] = {}
        if editable_ids:
            now = utc_now()
            occupied_rows = (
                await db.execute(
                    select(
                        SessionBooking.session_id,
                        func.coalesce(func.sum(SessionBooking.party_size), 0),
                    )
                    .where(
                        SessionBooking.session_id.in_(editable_ids),
                        or_(
                            SessionBooking.status == SessionBookingStatus.CONFIRMED,
                            and_(
                                SessionBooking.status == SessionBookingStatus.PENDING,
                                or_(
                                    SessionBooking.expires_at.is_(None),
                                    SessionBooking.expires_at > now,
                                ),
                            ),
                        ),
                    )
                    .group_by(SessionBooking.session_id)
                )
            ).all()
            occupied_by_session = {
                session_id: int(occupied or 0) for session_id, occupied in occupied_rows
            }
            guest_rows = (
                await db.execute(
                    select(GuestPass.session_id, func.count(GuestPass.id))
                    .where(
                        GuestPass.session_id.in_(editable_ids),
                        GuestPass.booking_mode == "reservation",
                        or_(
                            GuestPass.status.in_(["confirmed", "attended"]),
                            and_(
                                GuestPass.status.in_(
                                    ["pending_payment", "payment_failed"]
                                ),
                                GuestPass.reservation_expires_at.is_not(None),
                                GuestPass.reservation_expires_at > now,
                            ),
                        ),
                    )
                    .group_by(GuestPass.session_id)
                )
            ).all()
            for session_id, occupied in guest_rows:
                occupied_by_session[session_id] = occupied_by_session.get(
                    session_id, 0
                ) + int(occupied or 0)
        requested_capacity = int(values["capacity"])
        for session in sessions:
            occupied = occupied_by_session.get(session.id, 0)
            if session.status in editable and requested_capacity < occupied:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"{session.title} already has {occupied} occupied places; "
                        f"capacity cannot be reduced to {requested_capacity}."
                    ),
                )

    updated_count = 0
    cancelled: list[Session] = []
    changed_snapshots: list[tuple[Session, dict]] = []
    for session in sessions:
        if session.status in editable:
            before = {
                "starts_at": session.starts_at,
                "ends_at": session.ends_at,
                "timezone": session.timezone,
                "pool_id": session.pool_id,
                "location_name": session.location_name,
            }
            if "capacity" in values and (
                session.pricing_expected_attendees is None
                or session.pricing_expected_attendees > int(values["capacity"])
            ):
                pricing_payload = pricing_payload_from_session(session)
                pricing_payload["capacity"] = int(values["capacity"])
                pricing_payload["pricing_expected_attendees"] = int(values["capacity"])
                normalized_pricing = normalize_pricing_payload(pricing_payload)
                for field, value in normalized_pricing.items():
                    if field == "pool_fee":
                        session.pool_fee = round(float(value) * 100)
                    else:
                        setattr(session, field, value)
            for field, value in values.items():
                setattr(session, field, value)
            resulting_start = values.get("starts_at", session.starts_at)
            resulting_end = values.get("ends_at", session.ends_at)
            if resulting_end is not None and resulting_end <= resulting_start:
                raise HTTPException(
                    status_code=422,
                    detail="Event end time must be later than its start time",
                )
            if values:
                updated_count += 1
                changed_snapshots.append((session, before))
        if payload.cancel and session.status not in {
            SessionStatus.CANCELLED,
            SessionStatus.COMPLETED,
        }:
            session.status = SessionStatus.CANCELLED
            cancelled.append(session)

    for changed_session, _before in changed_snapshots:
        await sync_legacy_session_rates(db, changed_session)

    await db.commit()

    settings = get_settings()
    for session, before in changed_snapshots:
        schedule_changed = (
            before["starts_at"] != session.starts_at
            or before["ends_at"] != session.ends_at
            or before["timezone"] != session.timezone
        )
        venue_changed = (
            before["pool_id"] != session.pool_id
            or before["location_name"] != session.location_name
        )
        if before["starts_at"] != session.starts_at:
            try:
                await reconcile_session_ride_schedule(
                    session_id=str(session.id),
                    old_starts_at=before["starts_at"].isoformat(),
                    new_starts_at=session.starts_at.isoformat(),
                    calling_service="sessions",
                )
            except Exception as exc:
                logger.warning(
                    "Could not reconcile ride departures for Event Session %s: %s",
                    session.id,
                    exc,
                )
        if schedule_changed or venue_changed:
            try:
                await reconcile_volunteer_session_schedule(
                    session_id=str(session.id),
                    old_starts_at=before["starts_at"].isoformat(),
                    old_ends_at=before["ends_at"].isoformat(),
                    new_starts_at=session.starts_at.isoformat(),
                    new_ends_at=session.ends_at.isoformat(),
                    old_timezone=before["timezone"],
                    new_timezone=session.timezone,
                    old_location_name=before["location_name"],
                    new_location_name=session.location_name,
                    calling_service="sessions",
                )
            except Exception as exc:
                logger.warning(
                    "Could not reconcile volunteer opportunities for Event Session %s: %s",
                    session.id,
                    exc,
                )

            member_ids = [
                str(member_id)
                for member_id in (
                    await db.execute(
                        select(SessionBooking.member_id).where(
                            SessionBooking.session_id == session.id,
                            SessionBooking.status.in_(
                                [
                                    SessionBookingStatus.PENDING,
                                    SessionBookingStatus.CONFIRMED,
                                ]
                            ),
                        )
                    )
                ).scalars()
            ]
            message = (
                f"{session.title} was updated. It now runs from "
                f"{session.starts_at.isoformat()} to {session.ends_at.isoformat()} "
                f"at {session.location_name or 'the updated venue'}. Your booking "
                "and amount paid are unchanged."
            )
            await dispatch_notification(
                type="session_updated",
                category="sessions",
                member_ids=sorted(set(member_ids)),
                title="Swimming session updated",
                body=message,
                action_url=f"/sessions/{session.id}",
                calling_service="sessions",
                metadata={"event_id": str(event_id), "session_id": str(session.id)},
            )
            guest_emails = list(
                (
                    await db.execute(
                        select(GuestPass.email).where(
                            GuestPass.session_id == session.id,
                            GuestPass.status.in_(["confirmed", "pending_payment"]),
                        )
                    )
                ).scalars()
            )
            for email in sorted(set(guest_emails)):
                try:
                    await internal_post(
                        service_url=settings.COMMUNICATIONS_SERVICE_URL,
                        path="/email/send",
                        calling_service="sessions",
                        json={
                            "to_email": email,
                            "subject": "Swimming session updated",
                            "body": message,
                        },
                    )
                except Exception as exc:
                    logger.warning(
                        "Could not notify guest %s for Event Session %s: %s",
                        email,
                        session.id,
                        exc,
                    )

    for session in cancelled:
        try:
            await internal_post(
                service_url=settings.COMMUNICATIONS_SERVICE_URL,
                path="/internal/communications/session-cancelled",
                calling_service="sessions",
                json={
                    "session_id": str(session.id),
                    "cancellation_reason": payload.cancellation_reason
                    or "Linked event cancelled",
                },
            )
        except Exception as exc:
            logger.warning(
                "Could not send cancellation notice for Event Session %s: %s",
                session.id,
                exc,
            )
        try:
            await cancel_opportunities_for_context(
                calling_service="sessions",
                session_id=str(session.id),
                reason=payload.cancellation_reason or "Linked event cancelled",
            )
        except Exception as exc:
            logger.warning(
                "Could not cancel volunteer opportunities for Event Session %s: %s",
                session.id,
                exc,
            )

    return EventSessionSyncResult(
        event_id=event_id,
        linked_count=len(sessions),
        updated_count=updated_count,
        cancelled_count=len(cancelled),
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

    booking_rows = (
        await db.execute(
            select(
                SessionBooking.member_id,
                SessionBooking.party_size,
            ).where(
                SessionBooking.session_id == session.id,
                SessionBooking.status == SessionBookingStatus.CONFIRMED,
            )
        )
    ).all()
    confirmed_member_ids = [str(member_id) for member_id, _ in booking_rows]
    occupied_slots = sum(int(party_size or 1) for _, party_size in booking_rows)
    coach_ids = (
        (
            await db.execute(
                select(SessionCoach.coach_id).where(
                    SessionCoach.session_id == session.id
                )
            )
        )
        .scalars()
        .all()
    )
    coach_member_ids = [str(coach_id) for coach_id in coach_ids]

    return SessionBasic(
        id=str(session.id),
        title=session.title,
        description=session.description,
        notes=session.notes,
        session_type=session.session_type.value,
        status=session.status.value,
        starts_at=session.starts_at.isoformat(),
        ends_at=session.ends_at.isoformat(),
        pool_id=str(session.pool_id) if session.pool_id else None,
        location_name=session.location_name,
        location_address=session.location_address,
        location=session.location.value if session.location else None,
        cohort_id=str(session.cohort_id) if session.cohort_id else None,
        club_id=(
            str(club_id) if (club_id := getattr(session, "club_id", None)) else None
        ),
        pod_id=str(session.pod_id) if session.pod_id else None,
        capacity=session.capacity,
        pool_fee=session.pool_fee,
        cohort_fee_mode=getattr(session, "cohort_fee_mode", None) or "included",
        guest_fee_kobo=getattr(session, "guest_fee_kobo", None),
        community_dropin_fee_kobo=getattr(session, "community_dropin_fee_kobo", None),
        visiting_club_fee_kobo=getattr(session, "visiting_club_fee_kobo", None),
        allows_community_dropins=getattr(session, "allows_community_dropins", False),
        allows_visiting_club_members=getattr(
            session, "allows_visiting_club_members", False
        ),
        ride_share_fee=session.ride_share_fee,
        occupied_slots=occupied_slots,
        confirmed_booking_member_ids=confirmed_member_ids,
        coach_member_ids=coach_member_ids,
        week_number=session.week_number,
        lesson_title=session.lesson_title,
        timezone=session.timezone,
    )


@router.get("/{session_id}/access", response_model=MemberSessionAccessResponse)
async def get_member_session_access(
    session_id: uuid.UUID,
    member_auth_id: str = Query(..., min_length=1, max_length=128),
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
) -> MemberSessionAccessResponse:
    """Return the backend-owned access decision used by payment services."""
    session = (
        await db.execute(select(Session).where(Session.id == session_id))
    ).scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    try:
        member = await get_member_by_auth_id(
            member_auth_id,
            calling_service="sessions",
        )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=503,
            detail="Could not verify the member profile. Please try again.",
        ) from exc
    if not member or not member.get("id"):
        raise HTTPException(status_code=404, detail="Member profile not found")
    try:
        member_id = uuid.UUID(str(member["id"]))
    except ValueError as exc:
        raise HTTPException(
            status_code=502,
            detail="Members service returned an invalid member profile",
        ) from exc

    booking = (
        await db.execute(
            select(SessionBooking).where(
                SessionBooking.session_id == session_id,
                SessionBooking.member_id == member_id,
                SessionBooking.status == SessionBookingStatus.CONFIRMED,
            )
        )
    ).scalar_one_or_none()

    if booking is None:
        member_payload = await get_member_session_access_payload(
            member_id=member_id,
            calling_service="sessions",
        )
    else:
        member_payload = {"id": str(member_id), "member_id": str(member_id)}

    access = await evaluate_session_access_for_member(
        session=session,
        member_payload=member_payload,
        now=utc_now(),
        calling_service="sessions",
        confirmed_booking=booking is not None,
    )
    access = await apply_session_rate(db, session, access)
    return MemberSessionAccessResponse(
        member_id=member_id,
        confirmed_booking=booking is not None,
        confirmed_booking_id=booking.id if booking else None,
        required_tier=access.required_tier,
        visible=access.visible,
        bookable=access.bookable,
        digest_eligible=access.digest_eligible,
        prompt_eligible=access.prompt_eligible,
        sign_in_allowed=access.sign_in_allowed,
        sign_in_eligible=access.sign_in_eligible,
        reason=access.reason,
        message=denial_message(access.reason) if access.reason else None,
        access_source=access.access_source,
        fee_amount_kobo=access.fee_amount_kobo,
        price_label=access.price_label,
        pricing_audience=access.pricing_audience,
        pricing_source=access.pricing_source,
        rate_code=access.rate_code,
        rate_id=uuid.UUID(access.rate_id) if access.rate_id else None,
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


@router.get("/{session_id}/confirmed-booking-member-ids", response_model=List[str])
async def get_confirmed_booking_member_ids(
    session_id: uuid.UUID,
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Member IDs with a CONFIRMED booking for this session (the 'expected to
    attend' set). attendance-service uses this to pre-fill the coach attendance
    sheet — default Present if booked, Absent if not."""
    rows = (
        (
            await db.execute(
                select(SessionBooking.member_id).where(
                    SessionBooking.session_id == session_id,
                    SessionBooking.status == SessionBookingStatus.CONFIRMED,
                )
            )
        )
        .scalars()
        .all()
    )
    return [str(m) for m in rows]


@router.get("/cohorts/{cohort_id}/completed-session-ids", response_model=List[str])
async def get_completed_session_ids_for_cohort(
    cohort_id: uuid.UUID,
    # datetime, not str: binds as timestamptz (str 500s the starts_at comparison)
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
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


@router.get("/cohorts/{cohort_id}/sessions", response_model=List[SessionBasic])
async def get_sessions_for_cohort_internal(
    cohort_id: uuid.UUID,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    statuses: str = Query(
        "scheduled,in_progress,completed",
        description="Comma-separated session statuses to include.",
    ),
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Get dated cohort sessions for reporting and academy integrations."""
    parsed_statuses: list[SessionStatus] = []
    invalid: list[str] = []
    for raw_status in [s.strip() for s in statuses.split(",") if s.strip()]:
        try:
            parsed_statuses.append(SessionStatus(raw_status))
        except ValueError:
            invalid.append(raw_status)
    if invalid:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid status value(s): {', '.join(invalid)}",
        )

    query = select(Session).where(Session.cohort_id == cohort_id)
    if parsed_statuses:
        query = query.where(Session.status.in_(parsed_statuses))
    if start_date:
        query = query.where(Session.starts_at >= start_date)
    if end_date:
        query = query.where(Session.starts_at <= end_date)
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
            pool_id=str(s.pool_id) if s.pool_id else None,
            location_name=s.location_name,
            location_address=s.location_address,
            location=s.location.value if s.location else None,
            cohort_id=str(s.cohort_id) if s.cohort_id else None,
            club_id=(
                str(club_id) if (club_id := getattr(s, "club_id", None)) else None
            ),
            pod_id=str(s.pod_id) if s.pod_id else None,
            capacity=s.capacity,
            pool_fee=s.pool_fee,
            cohort_fee_mode=getattr(s, "cohort_fee_mode", None) or "included",
            week_number=s.week_number,
            lesson_title=s.lesson_title,
            timezone=s.timezone,
        )
        for s in sessions
    ]


@router.post(
    "/cohorts/{cohort_id}/generate",
    response_model=GenerateCohortSessionsResponse,
)
async def generate_cohort_sessions(
    cohort_id: uuid.UUID,
    body: GenerateCohortSessionsRequest,
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Generate the weekly cohort_class sessions for a date window.

    Called by academy-service when a cohort extension is approved so the added
    weeks get sessions automatically (mirroring the create-cohort wizard).
    Idempotent: dates that already have a session are skipped.
    """
    from services.sessions_service.services.cohort_sessions import (
        generate_sessions_for_cohort,
    )

    result = await generate_sessions_for_cohort(
        db, cohort_id, body.from_date, body.to_date
    )
    await db.commit()

    from services.sessions_service.services.notifications import (
        trigger_session_published_notifications,
    )

    for entry in result.get("created_sessions", []):
        await trigger_session_published_notifications(
            session_id=entry["session_id"],
            starts_at=datetime.fromisoformat(entry["starts_at"]),
        )

    return GenerateCohortSessionsResponse(**result)


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


@router.post(
    "/bookings/bundle/reserve",
    response_model=BundleBookingReserveResponse,
)
async def reserve_bundle_bookings(
    payload: BundleBookingReserveRequest,
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
) -> BundleBookingReserveResponse:
    """Reserve capacity and snapshot server-owned prices for a bundle payment."""
    member = await get_member_by_auth_id(
        payload.member_auth_id, calling_service="sessions"
    )
    if not member:
        raise HTTPException(status_code=404, detail="Member profile not found")
    member_id = uuid.UUID(str(member["id"]))

    sessions = (
        (
            await db.execute(
                select(Session)
                .where(Session.id.in_(payload.session_ids))
                .order_by(Session.id)
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    session_map = {session.id: session for session in sessions}
    missing = [
        session_id
        for session_id in payload.session_ids
        if session_id not in session_map
    ]
    if missing:
        raise HTTPException(
            status_code=404, detail="One or more sessions were not found"
        )

    existing_rows = (
        (
            await db.execute(
                select(SessionBooking).where(
                    SessionBooking.member_id == member_id,
                    SessionBooking.session_id.in_(payload.session_ids),
                )
            )
        )
        .scalars()
        .all()
    )
    existing_by_session = {booking.session_id: booking for booking in existing_rows}
    now = utc_now()

    for session_id in payload.session_ids:
        existing = existing_by_session.get(session_id)
        if existing is None:
            continue
        if existing.status == SessionBookingStatus.CONFIRMED:
            raise HTTPException(
                status_code=409,
                detail=f"Session {session_id} is already booked",
            )
        if (
            existing.status == SessionBookingStatus.PENDING
            and (existing.expires_at is None or existing.expires_at > now)
            and existing.payment_intent_id != payload.payment_intent_id
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    "A payment is already in progress for one or more selected "
                    "sessions. Complete it or wait for the reservation to expire."
                ),
            )

    member_payload = await get_member_session_access_payload(
        member_id=member_id,
        calling_service="sessions",
    )
    access_by_session = {}
    for session_id in payload.session_ids:
        session = session_map[session_id]
        access = await evaluate_session_access_for_member(
            session=session,
            member_payload=member_payload,
            now=now,
            calling_service="sessions",
        )
        access = await apply_session_rate(db, session, access)
        if not access.bookable:
            raise HTTPException(
                status_code=403,
                detail=f"{session.title}: {denial_message(access.reason)}",
            )
        access_by_session[session_id] = access

    lines: list[BundleBookingLineResponse] = []
    for session_id in payload.session_ids:
        session = session_map[session_id]
        await assert_booking_capacity(
            db,
            session=session,
            member_id=member_id,
            new_party_size=1,
        )
        access = access_by_session[session_id]
        fee_kobo = int(access.fee_amount_kobo or 0)
        booking = existing_by_session.get(session_id)
        if booking is None:
            booking = SessionBooking(
                session_id=session_id,
                member_id=member_id,
                member_auth_id=payload.member_auth_id,
                status=SessionBookingStatus.PENDING,
                channel=BookingChannel.BUNDLE_CART,
                party_size=1,
                fee_amount_kobo=fee_kobo,
                member_fee_amount_kobo=fee_kobo,
                access_source=access.access_source,
                pricing_audience=access.pricing_audience,
                pricing_source=access.pricing_source,
                rate_id=uuid.UUID(access.rate_id) if access.rate_id else None,
                rate_code=access.rate_code,
                payment_intent_id=payload.payment_intent_id,
                booked_at=now,
                expires_at=now + timedelta(minutes=PENDING_TTL_MINUTES),
            )
            db.add(booking)
        else:
            booking.member_auth_id = payload.member_auth_id
            booking.status = SessionBookingStatus.PENDING
            booking.channel = BookingChannel.BUNDLE_CART
            booking.party_size = 1
            booking.fee_amount_kobo = fee_kobo
            booking.member_fee_amount_kobo = fee_kobo
            booking.access_source = access.access_source
            booking.pricing_audience = access.pricing_audience
            booking.pricing_source = access.pricing_source
            booking.rate_id = uuid.UUID(access.rate_id) if access.rate_id else None
            booking.rate_code = access.rate_code
            booking.payment_intent_id = payload.payment_intent_id
            booking.wallet_transaction_id = None
            booking.confirmed_at = None
            booking.cancelled_at = None
            booking.booked_at = now
            booking.expires_at = now + timedelta(minutes=PENDING_TTL_MINUTES)
        await db.flush()
        lines.append(
            BundleBookingLineResponse(
                session_id=session_id,
                booking_id=booking.id,
                amount_kobo=fee_kobo,
            )
        )

    await db.commit()
    return BundleBookingReserveResponse(
        member_id=member_id,
        payment_intent_id=payload.payment_intent_id,
        pool_total_kobo=sum(line.amount_kobo for line in lines),
        lines=lines,
    )


@router.post(
    "/bookings/bundle/confirm",
    response_model=BundleBookingConfirmResponse,
)
async def confirm_bundle_bookings(
    payload: BundleBookingConfirmRequest,
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
) -> BundleBookingConfirmResponse:
    """Confirm a paid bundle atomically and idempotently.

    Expired holds may still be confirmed after a delayed provider callback, but
    only while every session remains upcoming and has capacity. Either every
    booking commits or none of them does.
    """
    booking_refs = (
        await db.execute(
            select(SessionBooking.id, SessionBooking.session_id)
            .where(SessionBooking.id.in_(payload.booking_ids))
            .order_by(SessionBooking.id)
        )
    ).all()
    if len({row.id for row in booking_refs}) != len(payload.booking_ids):
        raise HTTPException(
            status_code=404, detail="One or more bookings were not found"
        )

    # Reserve takes session locks before mutating booking rows. Confirmation
    # uses the same global order so checkout and callback traffic cannot form
    # a sessions->bookings / bookings->sessions deadlock cycle.
    session_ids = sorted({row.session_id for row in booking_refs})
    sessions = (
        (
            await db.execute(
                select(Session)
                .where(Session.id.in_(session_ids))
                .order_by(Session.id)
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    session_map = {session.id: session for session in sessions}
    if len(session_map) != len(session_ids):
        raise HTTPException(
            status_code=404, detail="One or more sessions were not found"
        )

    rows = (
        (
            await db.execute(
                select(SessionBooking)
                .where(SessionBooking.id.in_(payload.booking_ids))
                .order_by(SessionBooking.id)
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    booking_map = {booking.id: booking for booking in rows}
    if len(booking_map) != len(payload.booking_ids):
        raise HTTPException(
            status_code=404, detail="One or more bookings were not found"
        )

    ordered_bookings = [booking_map[booking_id] for booking_id in payload.booking_ids]
    for booking in ordered_bookings:
        if booking.member_auth_id != payload.member_auth_id:
            raise HTTPException(
                status_code=409,
                detail="A booking belongs to a different member",
            )
        if booking.payment_intent_id != payload.payment_intent_id:
            raise HTTPException(
                status_code=409,
                detail="A booking belongs to a different payment intent",
            )
        if booking.status not in {
            SessionBookingStatus.PENDING,
            SessionBookingStatus.EXPIRED,
            SessionBookingStatus.CONFIRMED,
        }:
            raise HTTPException(
                status_code=422,
                detail=f"Cannot confirm a booking with status={booking.status.value}",
            )

    now = utc_now()
    for booking in ordered_bookings:
        if booking.status == SessionBookingStatus.CONFIRMED:
            continue
        session = session_map[booking.session_id]
        if session.status != SessionStatus.SCHEDULED or session.starts_at <= now:
            raise HTTPException(
                status_code=409,
                detail="A paid reservation can no longer be confirmed",
            )

    needs_capacity_check = [
        booking
        for booking in ordered_bookings
        if booking.status != SessionBookingStatus.CONFIRMED
        and (
            booking.status == SessionBookingStatus.EXPIRED
            or (booking.expires_at is not None and booking.expires_at <= now)
        )
    ]
    if needs_capacity_check:
        for booking in needs_capacity_check:
            session = session_map[booking.session_id]
            if session.status != SessionStatus.SCHEDULED or session.starts_at <= now:
                raise HTTPException(
                    status_code=409,
                    detail="An expired reservation can no longer be restored",
                )
            await assert_booking_capacity(
                db,
                session=session,
                member_id=booking.member_id,
                new_party_size=booking.party_size,
            )

    for booking in ordered_bookings:
        if booking.status != SessionBookingStatus.CONFIRMED:
            booking.status = SessionBookingStatus.CONFIRMED
            booking.confirmed_at = now
            booking.expires_at = None
        if (
            payload.wallet_transaction_id is not None
            and booking.wallet_transaction_id is None
        ):
            booking.wallet_transaction_id = payload.wallet_transaction_id

    email_keys = []
    details = payload.confirmation_details
    weights = [row.fee_amount_kobo for row in ordered_bookings]
    cash_parts = (
        allocate_total(round(details.amount_paid * 100), weights) if details else []
    )
    bubble_parts = (
        allocate_total(details.bubbles_applied or 0, weights) if details else []
    )
    for index, booking in enumerate(ordered_bookings):
        snapshot = details.model_dump(exclude_none=True) if details else None
        if snapshot:
            snapshot.update(
                amount_paid=cash_parts[index] / 100,
                bubbles_applied=bubble_parts[index],
                bubbles_amount_ngn=bubble_parts[index] * 100,
                bundle_info=f"Session {index + 1} of {len(ordered_bookings)} in your booking",
            )
        email_keys.append(
            await queue_confirmation(db, booking.id, payment_details=snapshot)
        )
    await db.commit()
    for booking, key in zip(ordered_bookings, email_keys):
        await db.refresh(booking)
        await deliver_confirmation(db, key)
        await sync_booking_attendance(booking)
    return BundleBookingConfirmResponse(
        confirmed=len(ordered_bookings),
        bookings=ordered_bookings,
    )


@router.post(
    "/bookings/bundle/release",
    response_model=BundleBookingReleaseResponse,
)
async def release_bundle_bookings(
    payload: BundleBookingReleaseRequest,
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
) -> BundleBookingReleaseResponse:
    """Release only pending reservations owned by an abandoned bundle intent."""
    rows = (
        (
            await db.execute(
                select(SessionBooking)
                .where(
                    SessionBooking.member_auth_id == payload.member_auth_id,
                    SessionBooking.payment_intent_id == payload.payment_intent_id,
                    SessionBooking.status == SessionBookingStatus.PENDING,
                )
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    now = utc_now()
    for booking in rows:
        booking.status = SessionBookingStatus.EXPIRED
        booking.expires_at = now
    if rows:
        await db.commit()
    return BundleBookingReleaseResponse(released=len(rows))


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


@router.get(
    "/bookings/{booking_id}",
    response_model=SessionBookingResponse,
)
async def get_booking_internal(
    booking_id: uuid.UUID,
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Service-role: fetch a single SessionBooking by id.

    Used by payments_service to generate an admin-issued pay link for a
    booking (purpose=session_booking). Returns 404 if not found.
    """
    booking = (
        await db.execute(select(SessionBooking).where(SessionBooking.id == booking_id))
    ).scalar_one_or_none()
    if booking is None:
        raise HTTPException(status_code=404, detail="Booking not found")
    return booking


@router.post("/{session_id}/walk-in-attendance", include_in_schema=False)
async def reconcile_admin_walk_in_attendance(
    session_id: uuid.UUID,
    payload: WalkInAttendanceReconcileRequest,
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Keep an unpaid admin walk-in booking aligned with corrected attendance.

    Only bookings created by the admin walk-in path are mutable here. A paid
    booking is never cancelled by an attendance correction. Replays are
    idempotent, and a mistaken ABSENT can be restored by marking PRESENT/LATE.
    """
    normalized = payload.status.strip().lower()
    if normalized not in {"present", "late", "absent", "excused", "cancelled"}:
        raise HTTPException(status_code=422, detail="Unsupported attendance status")

    booking = (
        await db.execute(
            select(SessionBooking)
            .where(
                SessionBooking.session_id == session_id,
                SessionBooking.member_id == payload.member_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if booking is None or booking.booking_source != "admin_walk_in":
        return {"action": "ignored", "reason": "not_admin_walk_in"}

    # Once money is linked, attendance can change but the financial booking
    # remains an immutable paid obligation/history record.
    if (
        booking.payment_intent_id is not None
        or booking.wallet_transaction_id is not None
    ):
        return {"action": "preserved", "reason": "paid_booking"}

    reversal_marker = "[walk_in_attendance_reversed]"
    notes = booking.notes or ""

    if normalized in {"absent", "excused", "cancelled"}:
        if booking.status == SessionBookingStatus.CONFIRMED:
            booking.status = SessionBookingStatus.CANCELLED
            booking.cancelled_at = utc_now()
            if reversal_marker not in notes:
                booking.notes = "\n".join(
                    filter(None, [notes, f"{reversal_marker} status={normalized}"])
                )
            await db.commit()
            return {"action": "cancelled"}
        return {"action": "unchanged"}

    # PRESENT/LATE restores only a booking that this reconciliation flow
    # previously cancelled; unrelated member/admin cancellations stay terminal.
    if booking.status == SessionBookingStatus.CANCELLED and reversal_marker in notes:
        booking.status = SessionBookingStatus.CONFIRMED
        booking.cancelled_at = None
        booking.confirmed_at = booking.confirmed_at or utc_now()
        await db.commit()
        return {"action": "restored"}

    return {"action": "unchanged"}


@router.post(
    "/bookings/{booking_id}/confirm",
    response_model=SessionBookingResponse,
)
async def internal_confirm_booking(
    booking_id: uuid.UUID,
    confirm_in: BookingConfirmRequest,
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
    request: Request = None,
):
    """Service-role variant of /sessions/bookings/{id}/confirm.

    Future: payments_service webhook calls this when a SESSION_BOOKING
    payment intent clears (so the booking gets confirmed even if the
    member closed the browser mid-checkout).
    """
    booking_ref = (
        await db.execute(
            select(SessionBooking.id, SessionBooking.session_id).where(
                SessionBooking.id == booking_id
            )
        )
    ).one_or_none()
    if booking_ref is None:
        raise HTTPException(status_code=404, detail="Booking not found")

    # Match the bundle lock order: session first, then booking. This lets an
    # expired payment callback safely restore capacity without deadlocking a
    # simultaneous reservation.
    session = (
        await db.execute(
            select(Session)
            .where(Session.id == booking_ref.session_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    booking = (
        await db.execute(
            select(SessionBooking)
            .where(SessionBooking.id == booking_id)
            .with_for_update()
        )
    ).scalar_one()
    if (
        confirm_in.member_auth_id is not None
        and booking.member_auth_id != confirm_in.member_auth_id
    ):
        raise HTTPException(
            status_code=409,
            detail="Booking belongs to a different member",
        )
    if (
        booking.payment_intent_id is not None
        and confirm_in.payment_intent_id is not None
        and booking.payment_intent_id != confirm_in.payment_intent_id
    ):
        raise HTTPException(
            status_code=409,
            detail="Booking belongs to a different payment intent",
        )
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
        key = await queue_confirmation(
            db,
            booking.id,
            payment_details=confirm_in.confirmation_details.model_dump(
                exclude_none=True
            )
            if confirm_in.confirmation_details
            else None,
        )
        await db.commit()
        if updated:
            await db.refresh(booking)
        await deliver_confirmation(db, key)
        await sync_booking_attendance(booking, preserve_existing=True)
        return booking
    now = utc_now()
    allow_historical_confirmation = bool(
        request
        and request.headers.get("X-Allow-Historical-Confirmation", "").lower()
        == "true"
    )
    historical_confirmation = bool(
        allow_historical_confirmation
        and session.starts_at <= now
        and session.status
        in {
            SessionStatus.SCHEDULED,
            SessionStatus.IN_PROGRESS,
            SessionStatus.COMPLETED,
        }
    )
    if not historical_confirmation and (
        session.status != SessionStatus.SCHEDULED or session.starts_at <= now
    ):
        raise HTTPException(
            status_code=409,
            detail="This paid reservation can no longer be confirmed",
        )
    if booking.status == SessionBookingStatus.EXPIRED and not historical_confirmation:
        await assert_booking_capacity(
            db,
            session=session,
            member_id=booking.member_id,
            new_party_size=booking.party_size,
        )
    elif booking.status not in {
        SessionBookingStatus.PENDING,
        SessionBookingStatus.EXPIRED,
    }:
        raise HTTPException(
            status_code=422,
            detail=f"Cannot confirm a booking with status={booking.status.value}.",
        )
    booking.status = SessionBookingStatus.CONFIRMED
    booking.confirmed_at = utc_now()
    booking.expires_at = None
    if confirm_in.payment_intent_id is not None:
        booking.payment_intent_id = confirm_in.payment_intent_id
    if confirm_in.wallet_transaction_id is not None:
        booking.wallet_transaction_id = confirm_in.wallet_transaction_id
    key = await queue_confirmation(
        db,
        booking.id,
        payment_details=confirm_in.confirmation_details.model_dump(exclude_none=True)
        if confirm_in.confirmation_details
        else None,
    )
    await db.commit()
    await db.refresh(booking)
    await deliver_confirmation(db, key)
    await sync_booking_attendance(booking, preserve_existing=True)
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
        await sync_booking_attendance(booking)

    return BulkBookingResponse(
        created=len(payload.items) - skipped,
        skipped=skipped,
        bookings=[
            SessionBookingResponse.model_validate(b, from_attributes=True)
            for b in created_rows
        ],
    )
