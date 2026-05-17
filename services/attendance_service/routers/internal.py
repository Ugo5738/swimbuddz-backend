"""Internal service-to-service endpoints for attendance-service.

These endpoints are authenticated with service_role JWT only.
They are NOT exposed through the gateway — only other backend services
call them directly via Docker network.
"""

import uuid
from collections import Counter
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_service_role
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from libs.common.datetime_utils import utc_now
from services.attendance_service.models import (
    AttendanceRecord,
    BookingChannel,
    SessionBooking,
    SessionBookingStatus,
)
from services.attendance_service.models.enums import AttendanceStatus
from services.attendance_service.schemas import (
    BulkBookingRequest,
    BulkBookingResponse,
    SessionBookingResponse,
)

router = APIRouter(prefix="/internal/attendance", tags=["internal"])


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class AttendanceRecordBasic(BaseModel):
    id: str
    session_id: str
    member_id: str
    status: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/member/{member_id}",
    response_model=List[AttendanceRecordBasic],
)
async def get_member_attendance(
    member_id: uuid.UUID,
    session_ids: Optional[str] = Query(
        None, description="Comma-separated session IDs to filter by"
    ),
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Get attendance records for a member, optionally filtered by session IDs."""
    query = select(AttendanceRecord).where(
        AttendanceRecord.member_id == member_id,
    )
    if session_ids:
        ids = [uuid.UUID(sid.strip()) for sid in session_ids.split(",") if sid.strip()]
        query = query.where(AttendanceRecord.session_id.in_(ids))

    result = await db.execute(query)
    records = result.scalars().all()

    return [
        AttendanceRecordBasic(
            id=str(r.id),
            session_id=str(r.session_id),
            member_id=str(r.member_id),
            status=r.status.value if hasattr(r.status, "value") else str(r.status),
        )
        for r in records
    ]


@router.get(
    "/session/{session_id}/member-ids",
    response_model=List[str],
)
async def get_session_attendee_member_ids(
    session_id: uuid.UUID,
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Return distinct member ID strings for everyone who has an attendance
    record for the given session.  Used by communications-service to find
    who should receive session-related notifications."""
    query = (
        select(AttendanceRecord.member_id)
        .where(AttendanceRecord.session_id == session_id)
        .distinct()
    )
    result = await db.execute(query)
    return [str(mid) for mid in result.scalars().all()]


# ---------------------------------------------------------------------------
# Reporting aggregation
# ---------------------------------------------------------------------------


class MemberAttendanceStats(BaseModel):
    """Aggregated attendance stats for a member over a date range."""

    total_present: int = 0
    total_late: int = 0
    total_absent: int = 0
    total_excused: int = 0
    total_sessions: int = 0
    by_type: dict | None = None
    by_day: dict | None = None
    by_location: dict | None = None
    favorite_day: str | None = None
    favorite_location: str | None = None
    weekly_attendance: list[bool] | None = None
    events_attended: int = 0
    total_pool_hours: float = 0.0


@router.get(
    "/stats/member/{member_auth_id}",
    response_model=MemberAttendanceStats,
)
async def get_member_attendance_stats(
    member_auth_id: str,
    date_from: datetime = Query(..., alias="from"),
    date_to: datetime = Query(..., alias="to"),
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Aggregate attendance stats for a member within a date range.

    Used by the reporting service for quarterly reports.
    The member_auth_id is matched against members.auth_id via the member_id FK.
    """
    from services.attendance_service.models.core import MemberRef

    # Look up member_id from auth_id
    member_result = await db.execute(
        select(MemberRef.id).where(MemberRef.auth_id == member_auth_id)
    )
    member_uuid = member_result.scalar_one_or_none()
    if member_uuid is None:
        return MemberAttendanceStats()

    # Get all attendance records in the date range
    result = await db.execute(
        select(AttendanceRecord).where(
            AttendanceRecord.member_id == member_uuid,
            AttendanceRecord.created_at >= date_from,
            AttendanceRecord.created_at <= date_to,
        )
    )
    records = result.scalars().all()

    if not records:
        return MemberAttendanceStats()

    # Count by status
    status_counts = Counter(
        r.status.value if hasattr(r.status, "value") else str(r.status) for r in records
    )

    total_present = status_counts.get("present", 0)
    total_late = status_counts.get("late", 0)

    # Count by day of week
    day_counts: Counter = Counter()
    for r in records:
        if r.status in (AttendanceStatus.PRESENT, AttendanceStatus.LATE):
            day_name = r.created_at.strftime("%A")
            day_counts[day_name] += 1

    favorite_day = day_counts.most_common(1)[0][0] if day_counts else None

    # Compute weekly attendance (which weeks had at least one session)
    from datetime import timedelta

    weeks_attended: dict[int, bool] = {}
    for r in records:
        if r.status in (AttendanceStatus.PRESENT, AttendanceStatus.LATE):
            week_num = r.created_at.isocalendar()[1]
            weeks_attended[week_num] = True

    # Build ordered list of weeks in the range — cap at the current week so
    # weeks that haven't happened yet don't reset the streak to 0.
    from libs.common.datetime_utils import utc_now

    end_iter = min(date_to, utc_now())
    weekly_attendance = []
    current = date_from
    while current <= end_iter:
        wk = current.isocalendar()[1]
        weekly_attendance.append(wk in weeks_attended)
        current += timedelta(weeks=1)

    # Compute pool hours from attended sessions
    # Fetch session durations from sessions service for attended session IDs
    attended_session_ids = list(
        {
            str(r.session_id)
            for r in records
            if r.status in (AttendanceStatus.PRESENT, AttendanceStatus.LATE)
        }
    )
    total_pool_hours = 0.0
    if attended_session_ids:
        try:
            from libs.common.config import get_settings
            from libs.common.service_client import internal_get

            _settings = get_settings()
            resp = await internal_get(
                service_url=_settings.SESSIONS_SERVICE_URL,
                path="/internal/sessions/durations",
                calling_service="attendance",
                params={"ids": ",".join(attended_session_ids)},
                timeout=10.0,
            )
            if resp.status_code == 200:
                durations = resp.json()
                # durations is a list of {"session_id": ..., "duration_hours": ...}
                raw_hours = sum(d.get("duration_hours", 0) for d in durations)
                # Subtract ~1 hour per session for warmups/rests (minimum 0)
                effective_hours = max(0, raw_hours - len(attended_session_ids) * 1.0)
                total_pool_hours = round(effective_hours, 1)
        except Exception:
            pass  # Graceful fallback — pool hours stays 0

    return MemberAttendanceStats(
        total_present=total_present,
        total_late=total_late,
        total_absent=status_counts.get("absent", 0),
        total_excused=status_counts.get("excused", 0),
        total_sessions=len(records),
        by_day=dict(day_counts) if day_counts else None,
        favorite_day=favorite_day,
        weekly_attendance=weekly_attendance,
        total_pool_hours=total_pool_hours,
    )


# ---------------------------------------------------------------------------
# A1 Phase 3.3: Corporate-bulk SessionBooking creation
# ---------------------------------------------------------------------------


@router.post("/bookings/bulk", response_model=BulkBookingResponse)
async def bulk_create_bookings(
    payload: BulkBookingRequest,
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Bulk-create SessionBookings for a corporate-wellness sponsor.

    Used by sponsor onboarding flows that pre-purchase N×M (sessions ×
    members) and want every (session, member) pair to land as a
    CONFIRMED SessionBooking in one call. Each row is tagged with
    ``channel=CORPORATE_BULK`` and the supplied ``corporate_program_id``
    so the bookings can later be traced to the sponsor.

    Idempotent: if a booking for (session, member) already exists,
    that pair is reported in ``skipped`` and the existing row is
    returned unchanged.
    """
    created: list[SessionBooking] = []
    skipped = 0
    now = utc_now()

    for item in payload.items:
        existing_q = select(SessionBooking).where(
            SessionBooking.session_id == item.session_id,
            SessionBooking.member_id == item.member_id,
        )
        existing = (await db.execute(existing_q)).scalar_one_or_none()
        if existing is not None:
            skipped += 1
            created.append(existing)
            continue

        booking = SessionBooking(
            session_id=item.session_id,
            member_id=item.member_id,
            member_auth_id=item.member_auth_id,
            status=SessionBookingStatus.CONFIRMED,  # sponsor-paid up front
            channel=BookingChannel.CORPORATE_BULK,
            fee_amount_kobo=item.fee_amount_kobo,
            corporate_program_id=payload.corporate_program_id,
            confirmed_at=now,
        )
        db.add(booking)
        created.append(booking)

    await db.commit()
    for booking in created:
        await db.refresh(booking)

    return BulkBookingResponse(
        created=len(payload.items) - skipped,
        skipped=skipped,
        bookings=[
            SessionBookingResponse.model_validate(b, from_attributes=True)
            for b in created
        ],
    )
