"""Make-up scheduling domain logic (Phase 0).

Pure functions for the Missed-Session policy
(docs/policy/MISSED_SESSION_AND_MAKEUP_POLICY.md) — NO DB or HTTP access. The
router gathers inputs (coach availability via members_service, the coach's
booked sessions, the learner's other sessions) and calls these. See
docs/design/AVAILABILITY_AND_MAKEUP_SCHEDULING_DESIGN.md §7–§8.

Time handling: availability is expressed in the coach's local timezone
(``calendar['timezone']``); recurring blocks are a local weekday + 'HH:MM'.
Everything is converted to tz-aware UTC for comparison and returned as UTC, so
callers serialize ISO-8601 and clients render in local time.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

# Policy defaults (§4). Overridable per coach (min_hours) / per call.
DEFAULT_SPACING_HOURS = 48
NOTICE_THRESHOLD_HOURS = 24
MAKEUP_WINDOW_DAYS = 14
DEFAULT_SLOT_MINUTES = 60

_UTC = ZoneInfo("UTC")
_WEEKDAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


@dataclass(frozen=True)
class Interval:
    """A half-open time interval [start, end) in tz-aware UTC."""

    start: datetime
    end: datetime


@dataclass(frozen=True)
class CoachSession:
    """An existing scheduled session a make-up learner could potentially join."""

    start: datetime
    end: datetime
    session_id: str
    title: str
    capacity: int
    booked_count: int

    @property
    def spots_left(self) -> int:
        return max(0, self.capacity - self.booked_count)

    @property
    def has_room(self) -> bool:
        return self.booked_count < self.capacity


@dataclass
class BookableSlot:
    """A candidate make-up option (tz-aware UTC) with policy spacing flags.

    ``kind`` is "open" (a dedicated gap in the coach's availability) or
    "join_session" (an existing session the learner can join — policy §1: a
    make-up needn't be 1:1). For "join_session", session_id / session_title /
    spots_left describe the session to join.
    """

    start: datetime
    end: datetime
    kind: str = "open"  # "open" | "join_session"
    session_id: str | None = None
    session_title: str | None = None
    spots_left: int | None = None
    ok: bool = True  # False when spacing warnings exist
    warnings: list[str] = field(default_factory=list)


def _parse_hhmm(value: str) -> time:
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


def _coach_tz(calendar: dict) -> ZoneInfo:
    try:
        return ZoneInfo(calendar.get("timezone") or "Africa/Lagos")
    except Exception:
        return ZoneInfo("Africa/Lagos")


def _blackout_ranges(calendar: dict) -> list[tuple[date, date]]:
    ranges: list[tuple[date, date]] = []
    for b in calendar.get("blackouts", []) or []:
        try:
            ranges.append(
                (date.fromisoformat(str(b["start"])), date.fromisoformat(str(b["end"])))
            )
        except (KeyError, ValueError, TypeError):
            continue
    return ranges


def _in_blackout(day: date, blackouts: list[tuple[date, date]]) -> bool:
    return any(start <= day <= end for start, end in blackouts)


def expand_availability(
    calendar: dict, *, window_start: date, window_end: date
) -> list[Interval]:
    """Expand recurring weekly blocks over [window_start, window_end] (inclusive,
    coach-local dates) into UTC intervals, skipping blackout dates."""
    tz = _coach_tz(calendar)
    recurring = calendar.get("recurring", []) or []
    blackouts = _blackout_ranges(calendar)
    intervals: list[Interval] = []
    day = window_start
    while day <= window_end:
        if not _in_blackout(day, blackouts):
            key = _WEEKDAY_KEYS[day.weekday()]
            for blk in recurring:
                if blk.get("weekday") != key:
                    continue
                try:
                    s = datetime.combine(day, _parse_hhmm(blk["start"]), tzinfo=tz)
                    e = datetime.combine(day, _parse_hhmm(blk["end"]), tzinfo=tz)
                except (KeyError, ValueError):
                    continue
                if e > s:
                    intervals.append(Interval(s.astimezone(_UTC), e.astimezone(_UTC)))
        day += timedelta(days=1)
    return intervals


def slice_into_slots(
    interval: Interval, *, slot_minutes: int, buffer_minutes: int = 0
) -> list[Interval]:
    """Slice an interval into fixed-length slots separated by an optional buffer."""
    slots: list[Interval] = []
    duration = timedelta(minutes=slot_minutes)
    step = timedelta(minutes=slot_minutes + buffer_minutes)
    cursor = interval.start
    while cursor + duration <= interval.end:
        slots.append(Interval(cursor, cursor + duration))
        cursor += step
    return slots


def _overlaps(a: Interval, b: Interval) -> bool:
    return a.start < b.end and b.start < a.end


def _adjacent_calendar_day(a: datetime, b: datetime, tz: ZoneInfo) -> bool:
    return abs((a.astimezone(tz).date() - b.astimezone(tz).date()).days) == 1


def _spacing_warnings(
    slot: Interval,
    learner_sessions: list[datetime],
    *,
    spacing_hours: int,
    tz: ZoneInfo,
) -> list[str]:
    warnings: list[str] = []
    for other in learner_sessions:
        gap_hours = abs((slot.start - other).total_seconds()) / 3600.0
        if gap_hours < spacing_hours:
            warnings.append(
                f"only {gap_hours:.0f}h from a session on "
                f"{other.astimezone(tz).date().isoformat()} (min {spacing_hours}h)"
            )
        elif _adjacent_calendar_day(slot.start, other, tz):
            warnings.append(
                "back-to-back day with a session on "
                f"{other.astimezone(tz).date().isoformat()}"
            )
    return warnings


def compute_bookable_slots(
    calendar: dict,
    *,
    window_start: date,
    window_end: date,
    coach_sessions: list[CoachSession] | None = None,
    learner_sessions: list[datetime] | None = None,
    min_hours_between: int | None = None,
) -> list[BookableSlot]:
    """Compute a coach's bookable make-up options for [window_start, window_end].

    Returns two kinds of option (policy §1 — a make-up needn't be 1:1):
      * "open" — a dedicated gap in the coach's published availability
        (availability − blackouts − every coach session, sliced into slots).
      * "join_session" — an existing coach session within the window that still
        has room; the learner joins it alongside others.

    Every coach session occupies the coach's time, so it blocks "open" slots;
    sessions with room *additionally* surface as "join_session" options. Spacing
    violations are *flagged, not removed* (the policy informs on spacing — §4 / D2).
    Joinable sessions are returned even when the coach has published no calendar.
    Pedagogical fit of a join option is the coach/admin's call (§3).
    """
    coach_sessions = coach_sessions or []
    learner_sessions = learner_sessions or []
    tz = _coach_tz(calendar)
    slot_minutes = int(calendar.get("slot_minutes") or DEFAULT_SLOT_MINUTES)
    buffer_minutes = int(calendar.get("buffer_minutes") or 0)
    spacing_hours = min_hours_between or DEFAULT_SPACING_HOURS

    occupied = [Interval(cs.start, cs.end) for cs in coach_sessions]
    out: list[BookableSlot] = []

    # 1. Open slots — gaps in published availability, minus every coach session.
    for block in expand_availability(
        calendar, window_start=window_start, window_end=window_end
    ):
        for slot in slice_into_slots(
            block, slot_minutes=slot_minutes, buffer_minutes=buffer_minutes
        ):
            if any(_overlaps(slot, o) for o in occupied):
                continue
            warnings = _spacing_warnings(
                slot, learner_sessions, spacing_hours=spacing_hours, tz=tz
            )
            out.append(
                BookableSlot(
                    start=slot.start,
                    end=slot.end,
                    kind="open",
                    ok=not warnings,
                    warnings=warnings,
                )
            )

    # 2. Joinable existing sessions — those that still have room.
    for cs in coach_sessions:
        if not cs.has_room:
            continue
        warnings = _spacing_warnings(
            Interval(cs.start, cs.end),
            learner_sessions,
            spacing_hours=spacing_hours,
            tz=tz,
        )
        out.append(
            BookableSlot(
                start=cs.start,
                end=cs.end,
                kind="join_session",
                session_id=cs.session_id,
                session_title=cs.title,
                spots_left=cs.spots_left,
                ok=not warnings,
                warnings=warnings,
            )
        )

    out.sort(key=lambda s: (s.start, s.kind))
    return out


# ---------------------------------------------------------------------------
# Eligibility helpers (§4 / §7) — pure decision functions
# ---------------------------------------------------------------------------


def notice_hours(now: datetime, original_start: datetime) -> float:
    """Hours of notice before the original session (negative if already passed)."""
    return (original_start - now).total_seconds() / 3600.0


def is_penalty_free(
    now: datetime,
    original_start: datetime,
    *,
    threshold_hours: int = NOTICE_THRESHOLD_HOURS,
) -> bool:
    """Whether a reschedule / cancellation avoids forfeit, based on notice.

    >= threshold (24h) notice → penalty-free; less, or a no-show → forfeit/grace.

    NOTE: this governs the *penalty* only. A reschedule is **not** an automatic
    entitlement — it always needs a genuine reason, which the admin judges
    (policy §3/§4). Enough notice avoids the forfeit; it never makes a
    reason-less reschedule self-serve.
    """
    return notice_hours(now, original_start) >= threshold_hours


def makeup_window_end(
    missed_date: date,
    *,
    block_end: date | None = None,
    window_days: int = MAKEUP_WINDOW_DAYS,
) -> date:
    """Last date a make-up may be taken: min(block end, missed + 14 days)."""
    by_days = missed_date + timedelta(days=window_days)
    if block_end is not None:
        return min(by_days, block_end)
    return by_days


def is_within_makeup_window(
    slot_date: date, missed_date: date, *, block_end: date | None = None
) -> bool:
    """Whether ``slot_date`` falls on/before the make-up window end."""
    return slot_date <= makeup_window_end(missed_date, block_end=block_end)
