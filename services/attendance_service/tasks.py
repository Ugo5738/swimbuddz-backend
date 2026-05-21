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
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select

from libs.common.config import get_settings
from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from libs.common.service_client import (
    dispatch_notification,
    get_admin_members,
    get_session_by_id,
    internal_get,
)
from libs.common.service_client.sessions import list_confirmed_bookings_since
from libs.db.config import AsyncSessionLocal
from services.attendance_service.models import (
    AttendanceRecord,
    AttendanceRole,
    AttendanceStatus,
)

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


# ---------------------------------------------------------------------------
# Stale-attendance nudge — sent BEFORE the no-show sweep auto-marks rows
# ---------------------------------------------------------------------------


async def _get_session_coach_ids(session_id: str) -> list[str]:
    """Service-to-service call: get coach member IDs for a session.

    Wraps the internal sessions endpoint. Returns [] on failure (the
    notification job is best-effort).
    """
    settings = get_settings()
    try:
        resp = await internal_get(
            service_url=settings.SESSIONS_SERVICE_URL,
            path=f"/internal/sessions/{session_id}/coaches",
            calling_service="attendance",
        )
        resp.raise_for_status()
        ids = resp.json()
        return [str(x) for x in (ids or [])]
    except Exception as exc:
        logger.warning("Failed to fetch coach ids for session %s: %s", session_id, exc)
        return []


async def notify_stale_attendance(*, lookback_hours: int = 24) -> dict:
    """Notify coaches + admins about sessions whose attendance is still
    unmarked some hours after they ended.

    Runs once daily a few hours before ``sweep_no_show_bookings`` so the
    coach has a window to mark attendance themselves before the system
    auto-creates ABSENT rows. Sends one notification per (session, coach)
    pair plus one to every admin.

    Idempotency is handled implicitly: the cron runs daily and the time
    window is exactly ``lookback_hours``, so a given session falls into
    the window at most once. No state table needed.
    """
    cutoff_upper = utc_now()
    cutoff_lower = cutoff_upper - timedelta(hours=lookback_hours)

    try:
        bookings = await list_confirmed_bookings_since(
            since_iso=cutoff_lower.isoformat(), calling_service="attendance"
        )
    except Exception as exc:
        logger.error("notify_stale_attendance: failed to fetch bookings: %s", exc)
        return {"error": str(exc)}

    # Group bookings by session, then filter to sessions that ended inside
    # the window AND still have ≥1 unmatched booking.
    bookings_by_session: dict[str, list[dict]] = defaultdict(list)
    for b in bookings:
        bookings_by_session[b["session_id"]].append(b)

    notifications_sent = 0
    sessions_with_stale = 0

    # Lazy-load admin recipients — we'll fan out to all admins per stale
    # session. (Admins are the safety net when no coach is assigned.)
    admin_ids: list[str] = []
    admins_loaded = False

    async with AsyncSessionLocal() as db:
        for session_id_str, session_bookings in bookings_by_session.items():
            session_uuid = uuid.UUID(session_id_str)
            session_data = await get_session_by_id(
                session_id_str, calling_service="attendance"
            )
            if session_data is None:
                continue
            ends_at = _parse_iso(session_data.get("ends_at"))
            if ends_at is None:
                continue
            # Only sessions that ended inside our window.
            if not (cutoff_lower <= ends_at <= cutoff_upper):
                continue

            # Count unmatched: bookings whose (session, member) tuple has
            # no AttendanceRecord at all. ABSENT counts as "unmatched"
            # because it was likely auto-created by an earlier run of the
            # no-show sweep; we still want the coach to confirm/override.
            unmatched_count = 0
            for b in session_bookings:
                member_uuid = uuid.UUID(b["member_id"])
                existing = (
                    await db.execute(
                        select(AttendanceRecord.status).where(
                            AttendanceRecord.session_id == session_uuid,
                            AttendanceRecord.member_id == member_uuid,
                        )
                    )
                ).scalar_one_or_none()
                if existing is None:
                    unmatched_count += 1
                elif str(existing).lower() == "absent":
                    unmatched_count += 1

            if unmatched_count == 0:
                continue

            sessions_with_stale += 1
            coach_ids = await _get_session_coach_ids(session_id_str)
            if not admins_loaded:
                try:
                    admins = await get_admin_members(calling_service="attendance")
                    admin_ids = [str(a.get("id")) for a in admins if a.get("id")]
                except Exception as exc:
                    logger.warning(
                        "notify_stale_attendance: admin lookup failed: %s", exc
                    )
                    admin_ids = []
                admins_loaded = True

            recipients = list({*coach_ids, *admin_ids})
            if not recipients:
                continue

            title = f"Attendance still unmarked: {session_data.get('title', 'session')}"
            body = (
                f"{unmatched_count} booking"
                f"{'' if unmatched_count == 1 else 's'} "
                f"haven't been marked yet. The system will auto-mark them "
                f"as absent overnight if you don't confirm."
            )
            resp = await dispatch_notification(
                type="attendance_stale_reminder",
                category="attendance",
                member_ids=recipients,
                title=title,
                body=body,
                action_url=f"/admin/attendance?session={session_id_str}",
                icon="alert-triangle",
                channels=["in_app"],
                metadata={
                    "session_id": session_id_str,
                    "unmatched_count": unmatched_count,
                },
                calling_service="attendance",
            )
            if resp is not None:
                notifications_sent += 1

    result = {
        "sessions_with_stale": sessions_with_stale,
        "notifications_sent": notifications_sent,
        "lookback_hours": lookback_hours,
    }
    logger.info("notify_stale_attendance: %s", result)
    return result
