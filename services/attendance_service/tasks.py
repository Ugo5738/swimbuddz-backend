"""Background reconciliation tasks for the attendance service.

Currently houses the nightly NO_SHOW sweep: for every CONFIRMED
SessionBooking whose session has ended without a matching
AttendanceRecord, create AttendanceRecord(status=ABSENT, booking_id=...)
on the member's behalf. This is how "no-show" enters the data model —
the booking itself stays CONFIRMED (its lifecycle ended cleanly; the
member just didn't show up), and the negative outcome is captured on
AttendanceRecord where every other attendance fact lives. Reporting
queries can then express "no-show rate" as a single-table query:
``AttendanceRecord.status='absent' AND booking_id IS NOT NULL``.

See docs/design/A1_SESSION_DISCRIMINATOR_REFACTOR.md §C.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from libs.common.service_client import get_session_by_id
from libs.db.config import AsyncSessionLocal
from services.attendance_service.models import (
    AttendanceRecord,
    AttendanceRole,
    AttendanceStatus,
    SessionBooking,
    SessionBookingStatus,
)
from sqlalchemy import and_, select
from sqlalchemy.orm import aliased

logger = get_logger(__name__)


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


async def sweep_no_show_bookings(*, lookback_days: int = 7) -> dict:
    """Mark NO_SHOW attendance for CONFIRMED bookings past their session end.

    For each CONFIRMED SessionBooking whose linked session's ``ends_at`` is
    in the past AND no AttendanceRecord exists for that (session, member),
    create an AttendanceRecord(status=ABSENT, booking_id=...).

    Bounded by ``lookback_days`` (default 7) so the job stays O(recent
    sessions) even on a large data set — bookings older than this are
    assumed to have already been swept.

    Returns a dict ``{checked, created, skipped}`` for logging.
    """
    cutoff_lower = utc_now() - timedelta(days=lookback_days)
    cutoff_upper = utc_now()

    checked = 0
    created = 0
    skipped_already_attended = 0
    skipped_session_lookup = 0
    skipped_session_future = 0

    async with AsyncSessionLocal() as db:
        # Pull confirmed bookings from the recent window. We can't filter by
        # session ends_at directly (sessions live in another service), so we
        # filter by booking timing and check the session per-booking.
        # An EXISTS subquery would prefilter (booking, attendance) pairs;
        # using a LEFT OUTER JOIN keeps the SQL portable for tests.
        attendance_alias = aliased(AttendanceRecord)
        stmt = (
            select(SessionBooking, attendance_alias)
            .outerjoin(
                attendance_alias,
                and_(
                    attendance_alias.session_id == SessionBooking.session_id,
                    attendance_alias.member_id == SessionBooking.member_id,
                ),
            )
            .where(
                SessionBooking.status == SessionBookingStatus.CONFIRMED,
                SessionBooking.booked_at >= cutoff_lower,
                SessionBooking.booked_at <= cutoff_upper,
            )
        )

        rows = (await db.execute(stmt)).all()
        for booking, existing in rows:
            checked += 1
            if existing is not None:
                # Member already has an AttendanceRecord — either showed up,
                # or a previous sweep already marked them ABSENT. Skip.
                skipped_already_attended += 1
                continue

            # Need the session to know whether it's actually ended.
            session_data = await get_session_by_id(
                str(booking.session_id), calling_service="attendance"
            )
            if session_data is None:
                # Session vanished (deleted in upstream service). Leave the
                # booking alone — admin can decide what to do.
                skipped_session_lookup += 1
                continue
            ends_at = _parse_iso(session_data.get("ends_at"))
            if ends_at is None or ends_at > utc_now():
                # Session hasn't ended yet (or has no parseable end time).
                # Defer to the next sweep.
                skipped_session_future += 1
                continue

            record = AttendanceRecord(
                session_id=booking.session_id,
                member_id=booking.member_id,
                status=AttendanceStatus.ABSENT,
                role=AttendanceRole.SWIMMER,
                booking_id=booking.id,
                notes="auto-marked NO_SHOW by nightly sweep",
            )
            db.add(record)
            created += 1

        if created > 0:
            await db.commit()

    result = {
        "checked": checked,
        "created": created,
        "skipped_already_attended": skipped_already_attended,
        "skipped_session_lookup": skipped_session_lookup,
        "skipped_session_future": skipped_session_future,
    }
    logger.info("sweep_no_show_bookings: %s", result)
    return result
