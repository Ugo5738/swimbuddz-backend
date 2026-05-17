"""Background reconciliation tasks for the attendance service.

The nightly NO_SHOW sweep: for every CONFIRMED SessionBooking
(retrieved from sessions_service via HTTP) whose session ended without
a matching AttendanceRecord, create AttendanceRecord(status=ABSENT,
booking_id=<>) on the member's behalf. This is how "no-show" enters the
data model — the booking itself stays CONFIRMED (its lifecycle ended
cleanly; the member just didn't show up), and the negative outcome is
captured on AttendanceRecord where every other attendance fact lives.

After A1 Phase 3.3 was relocated to sessions_service, this task is
cross-service: pull candidates from sessions_service, check each against
local AttendanceRecord, create ABSENT rows for misses.

See docs/design/A1_SESSION_DISCRIMINATOR_REFACTOR.md §C.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Optional

from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from libs.common.service_client import get_session_by_id
from libs.common.service_client.sessions import list_confirmed_bookings_since
from libs.db.config import AsyncSessionLocal
from services.attendance_service.models import (
    AttendanceRecord,
    AttendanceRole,
    AttendanceStatus,
)
from sqlalchemy import select

logger = get_logger(__name__)


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


async def sweep_no_show_bookings(*, lookback_days: int = 7) -> dict:
    """Create ABSENT AttendanceRecords for CONFIRMED bookings past session end
    that have no attendance row.

    Bounded by ``lookback_days`` (default 7) so the job stays O(recent
    bookings) — anything older is assumed already swept.
    """
    cutoff_lower = (utc_now() - timedelta(days=lookback_days)).isoformat()

    checked = 0
    created = 0
    skipped_already_attended = 0
    skipped_session_lookup = 0
    skipped_session_future = 0

    try:
        bookings = await list_confirmed_bookings_since(
            since_iso=cutoff_lower, calling_service="attendance"
        )
    except Exception as exc:
        logger.error("sweep_no_show_bookings: failed to fetch bookings: %s", exc)
        return {"error": str(exc)}

    async with AsyncSessionLocal() as db:
        for booking in bookings:
            checked += 1
            session_id = uuid.UUID(booking["session_id"])
            member_id = uuid.UUID(booking["member_id"])
            booking_id = uuid.UUID(booking["id"])

            # Skip if an attendance record already exists for this
            # (session, member) — either the member showed up, or a previous
            # sweep already marked them ABSENT.
            existing = (
                await db.execute(
                    select(AttendanceRecord).where(
                        AttendanceRecord.session_id == session_id,
                        AttendanceRecord.member_id == member_id,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                skipped_already_attended += 1
                continue

            # Session has to have ENDED for the no-show to be a fact.
            session_data = await get_session_by_id(
                str(session_id), calling_service="attendance"
            )
            if session_data is None:
                skipped_session_lookup += 1
                continue
            ends_at = _parse_iso(session_data.get("ends_at"))
            if ends_at is None or ends_at > utc_now():
                skipped_session_future += 1
                continue

            record = AttendanceRecord(
                session_id=session_id,
                member_id=member_id,
                status=AttendanceStatus.ABSENT,
                role=AttendanceRole.SWIMMER,
                booking_id=booking_id,
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
