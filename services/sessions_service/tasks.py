"""Background reconciliation tasks for sessions service.

Currently houses:

* ``sweep_expired_pending_bookings`` — flips SessionBookings whose
  PENDING TTL has elapsed without payment to status=EXPIRED, freeing
  the seat. Runs every 5 minutes via the worker cron.

The nightly NO_SHOW sweep lives in attendance_service (because it
creates AttendanceRecord rows in that service's database); it calls
``list_confirmed_bookings_since`` here to fetch the candidate
bookings. See ``services/attendance_service/tasks.py``.

See docs/design/A1_SESSION_DISCRIMINATOR_REFACTOR.md §C.
"""

from __future__ import annotations

from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from libs.db.config import AsyncSessionLocal
from services.sessions_service.models import SessionBooking, SessionBookingStatus
from sqlalchemy import select, update

logger = get_logger(__name__)


async def sweep_expired_pending_bookings() -> dict:
    """Flip expired PENDING bookings to EXPIRED.

    A PENDING booking has ``expires_at = booked_at + 15 min`` set at
    creation. This sweep runs every 5 minutes; anything PENDING whose
    expires_at < now() becomes EXPIRED.

    Returns ``{checked, expired}`` for logging.
    """
    now = utc_now()
    async with AsyncSessionLocal() as db:
        candidates = (
            (
                await db.execute(
                    select(SessionBooking).where(
                        SessionBooking.status == SessionBookingStatus.PENDING,
                        SessionBooking.expires_at.is_not(None),
                        SessionBooking.expires_at < now,
                    )
                )
            )
            .scalars()
            .all()
        )
        n = len(candidates)
        if n == 0:
            result = {"checked": 0, "expired": 0}
            logger.info("sweep_expired_pending_bookings: %s", result)
            return result

        ids = [b.id for b in candidates]
        await db.execute(
            update(SessionBooking)
            .where(SessionBooking.id.in_(ids))
            .values(status=SessionBookingStatus.EXPIRED, updated_at=now)
        )
        await db.commit()

    result = {"checked": n, "expired": n}
    logger.info("sweep_expired_pending_bookings: %s", result)
    return result
