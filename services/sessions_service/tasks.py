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
from libs.common.service_client import (
    complete_makeup_obligation,
    get_member_attendance,
)
from libs.db.config import AsyncSessionLocal
from services.sessions_service.models import (
    MakeupBooking,
    MakeupStatus,
    Session,
    SessionBooking,
    SessionBookingStatus,
)
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


def _makeup_outcome(statuses: set[str | None]) -> MakeupStatus | None:
    """Outcome for a make-up given the learner's attendance statuses.

    PRESENT/LATE -> COMPLETED; ABSENT -> FORFEITED; otherwise (no decisive
    record, EXCUSED, CANCELLED) -> None (leave CONFIRMED for the next sweep).
    """
    if statuses & {"present", "late"}:
        return MakeupStatus.COMPLETED
    if "absent" in statuses:
        return MakeupStatus.FORFEITED
    return None


async def sweep_complete_makeups() -> dict:
    """Reconcile delivered make-ups against attendance.

    For each CONFIRMED MakeupBooking whose scheduled session has ended, read the
    learner's attendance on that session: PRESENT/LATE -> COMPLETED, ABSENT ->
    FORFEITED. No decisive record yet -> left for the next sweep.

    Returns ``{checked, completed, forfeited}`` for logging.
    """
    now = utc_now()
    checked = completed = forfeited = 0
    obligations_to_complete: list[str] = []
    async with AsyncSessionLocal() as db:
        makeups = (
            (
                await db.execute(
                    select(MakeupBooking)
                    .join(Session, Session.id == MakeupBooking.scheduled_session_id)
                    .where(
                        MakeupBooking.status == MakeupStatus.CONFIRMED,
                        Session.ends_at < now,
                    )
                )
            )
            .scalars()
            .all()
        )
        for makeup in makeups:
            checked += 1
            try:
                records = await get_member_attendance(
                    str(makeup.learner_member_id),
                    session_ids=[str(makeup.scheduled_session_id)],
                    calling_service="sessions",
                )
            except Exception as exc:  # noqa: BLE001 — best-effort; retry next sweep
                logger.warning(
                    "sweep_complete_makeups: attendance fetch failed for %s: %s",
                    makeup.id,
                    exc,
                )
                continue
            outcome = _makeup_outcome({r.get("status") for r in records})
            if outcome is MakeupStatus.COMPLETED:
                makeup.status = MakeupStatus.COMPLETED
                makeup.completed_at = now
                completed += 1
                if makeup.obligation_id is not None:
                    obligations_to_complete.append(str(makeup.obligation_id))
            elif outcome is MakeupStatus.FORFEITED:
                makeup.status = MakeupStatus.FORFEITED
                forfeited += 1
        if completed or forfeited:
            await db.commit()

    # Close the payout loop: a completed make-up completes its cohort obligation
    # so the coach is paid for delivery (best-effort; retried next sweep on fail).
    for obligation_id in obligations_to_complete:
        try:
            await complete_makeup_obligation(obligation_id, calling_service="sessions")
        except Exception as exc:  # noqa: BLE001 — best-effort, logged
            logger.warning(
                "sweep_complete_makeups: obligation %s completion failed: %s",
                obligation_id,
                exc,
            )

    result = {"checked": checked, "completed": completed, "forfeited": forfeited}
    logger.info("sweep_complete_makeups: %s", result)
    return result
