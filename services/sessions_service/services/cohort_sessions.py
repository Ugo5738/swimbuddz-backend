"""Cohort weekly-session generation (business logic, DB access, no HTTP).

The create-cohort wizard (frontend) lays down one COHORT_CLASS session per
week for the cohort's planned duration. Cohort *extensions*, however, are
approved in academy-service and historically only moved ``cohort.end_date`` —
no sessions were created for the added weeks, forcing admins to hand-create
each one. ``generate_sessions_for_cohort`` closes that gap: given a date
window it replicates the cohort's existing weekly cadence into the new weeks.

Schedule is *inferred* from the cohort's own sessions (the cohort row carries
no weekly-schedule fields), so this needs no schema change and naturally
matches whatever day/time the cohort actually runs — including a mid-cohort
time change (it anchors on the most recent weekly session, not the first).
"""

import re
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.datetime_utils import utc_now
from services.sessions_service.models import Session, SessionStatus, SessionType

_WEEK = timedelta(days=7)
# Safety cap so a pathological date range can't spin forever / flood the table.
_MAX_WEEKS = 60
_WEEK_TITLE_RE = re.compile(r"^\s*Week\s+\d+\s*-\s*(.*)$", re.IGNORECASE)


async def generate_sessions_for_cohort(
    db: AsyncSession,
    cohort_id: uuid.UUID,
    from_date: datetime,
    to_date: datetime,
) -> dict:
    """Create weekly COHORT_CLASS sessions in the half-open window (from_date, to_date].

    The most recent weekly session on/before ``from_date`` is used as the
    template for weekday, time-of-day, duration, location, capacity and fees.
    New sessions step forward in 7-day increments — Nigeria observes no DST, so
    adding exactly one week preserves the local wall-clock time even though
    ``starts_at`` is stored in UTC.

    Idempotent at day granularity: any target date that already has a session
    for this cohort is skipped, so re-running (or running after an admin already
    hand-created some weeks) never double-creates.

    Returns ``{"created": int, "skipped": int, "week_numbers": [...], "reason": str?}``.
    The caller is responsible for committing the transaction.
    """
    if to_date <= from_date:
        return {"created": 0, "skipped": 0, "week_numbers": [], "reason": "empty range"}

    existing = (
        (
            await db.execute(
                select(Session)
                .where(
                    Session.cohort_id == cohort_id,
                    Session.session_type == SessionType.COHORT_CLASS,
                )
                .order_by(Session.starts_at.desc())
            )
        )
        .scalars()
        .all()
    )
    if not existing:
        # No prior session to infer the schedule from. Refuse rather than guess;
        # the caller logs this and an admin can seed the first session.
        return {"created": 0, "skipped": 0, "week_numbers": [], "reason": "no template"}

    existing_dates = {s.starts_at.date() for s in existing}
    max_week = max((s.week_number or 0) for s in existing)

    # Template = most recent *weekly* class on/before from_date (prefer rows
    # that carry a week_number — regular weekly classes — over ad-hoc make-ups
    # which are created without one). Fall back to the most recent session.
    template = (
        next(
            (
                s
                for s in existing
                if s.starts_at <= from_date and s.week_number is not None
            ),
            None,
        )
        or next((s for s in existing if s.starts_at <= from_date), None)
        or existing[0]
    )

    duration = template.ends_at - template.starts_at
    title_match = _WEEK_TITLE_RE.match(template.title or "")
    title_base = title_match.group(1) if title_match else (template.title or "Session")

    created = 0
    skipped = 0
    week_numbers: list[int] = []
    wk = max_week
    start = template.starts_at
    for _ in range(_MAX_WEEKS):
        start = start + _WEEK
        if start <= from_date:
            # Weeks at/before the window start (the gap between the template and
            # from_date) are not part of the extension — skip without numbering.
            continue
        if start > to_date:
            break
        wk += 1
        if start.date() in existing_dates:
            skipped += 1
            continue
        session = Session(
            session_type=SessionType.COHORT_CLASS,
            status=SessionStatus.SCHEDULED,
            title=f"Week {wk} - {title_base}",
            starts_at=start,
            ends_at=start + duration,
            timezone=template.timezone,
            pool_id=template.pool_id,
            location=template.location,
            location_name=template.location_name,
            location_address=template.location_address,
            capacity=template.capacity,
            pool_fee=template.pool_fee,
            ride_share_fee=template.ride_share_fee,
            allows_guests=template.allows_guests,
            max_guests_per_booking=template.max_guests_per_booking,
            cohort_id=cohort_id,
            week_number=wk,
            published_at=utc_now(),
        )
        db.add(session)
        existing_dates.add(start.date())
        week_numbers.append(wk)
        created += 1

    await db.flush()
    return {"created": created, "skipped": skipped, "week_numbers": week_numbers}
