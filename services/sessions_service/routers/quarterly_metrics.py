"""Guest/group booking adoption, calendar delivery and canonical visits."""

from collections import Counter
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_service_role
from libs.auth.models import AuthUser
from libs.common.reporting import ReportingSection, enum_value, reporting_window
from libs.db.session import get_async_db
from services.sessions_service.models import (
    Session,
    SessionBooking,
    BookingGuest,
    GuestPass,
)
from services.sessions_service.routers.internal import get_session_detailed_stats

router = APIRouter(prefix="/internal/sessions", tags=["internal"])


@router.get("/reporting-summary", response_model=ReportingSection)
async def get_reporting_summary(
    date_from: datetime = Query(..., alias="from"),
    date_to: datetime = Query(..., alias="to"),
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    start, end = reporting_window(date_from, date_to)
    # Count reservations by scheduled visit date, including cancelled checkout
    # in a separate state bucket. It must never become an attended visit.
    booking_rows = (
        (
            await db.execute(
                select(SessionBooking)
                .join(Session, Session.id == SessionBooking.session_id)
                .where(
                    Session.starts_at >= start,
                    Session.starts_at < end,
                    SessionBooking.party_size > 1,
                )
            )
        )
        .scalars()
        .all()
    )
    guests = (
        (
            await db.execute(
                select(func.count(BookingGuest.id)).where(
                    BookingGuest.booking_id.in_([b.id for b in booking_rows]),
                )
            )
        ).scalar_one()
        if booking_rows
        else 0
    )
    passes = (
        (
            await db.execute(
                select(GuestPass)
                .join(Session, Session.id == GuestPass.session_id)
                .where(
                    Session.starts_at >= start,
                    Session.starts_at < end,
                )
            )
        )
        .scalars()
        .all()
    )
    detail = await get_session_detailed_stats(
        date_from.isoformat(), date_to.isoformat(), _, db
    )
    attendance_available = detail.attendance_available or detail.total_sessions == 0
    return ReportingSection(
        metrics={
            "calendar_sessions": detail.total_sessions,
            "completed_sessions": sum(
                s.get("status") == "completed" for s in detail.session_details or []
            ),
            "sessions_with_checkins": sum(
                int(s.get("attendance") or 0) > 0 for s in detail.session_details or []
            )
            if attendance_available
            else None,
            "member_checkins": detail.member_attendance_records
            if attendance_available
            else None,
            "guest_checkins": detail.guest_attendance_records
            if attendance_available
            else None,
            "group_bookings": len(booking_rows),
            "confirmed_group_bookings": sum(
                enum_value(b.status) == "confirmed" for b in booking_rows
            ),
            "attached_guest_reservations": guests,
            "guest_passes": len(passes),
            "attended_guest_passes": sum(p.status == "attended" for p in passes),
            "converted_guest_passes_as_of_snapshot": sum(
                p.converted_member_id is not None for p in passes
            ),
        },
        details={
            "sessions": detail.session_details or [],
            "attendance_available": attendance_available,
            "booking_states": dict(Counter(enum_value(b.status) for b in booking_rows)),
        },
        notes=[
            "Calendar sessions and completed-status sessions are separate counts. Missing check-ins do not establish that a scheduled session was cancelled or unattended.",
            "Guest/group reservations use session date. Check-ins come from Attendance; guest conversions are current linkage, not conversion-date events.",
        ]
        + (
            []
            if attendance_available
            else ["Attendance source unavailable: visit counts are unknown, not zero."]
        ),
    )
