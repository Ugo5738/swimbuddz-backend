"""Unit tests for make-up scheduling pure functions (no DB).

Covers slot expansion, busy removal, blackouts, spacing flags, and the
eligibility helpers. See
docs/design/AVAILABILITY_AND_MAKEUP_SCHEDULING_DESIGN.md §7–§8.
"""

from datetime import date, datetime, timezone

from services.sessions_service.services.makeup_scheduling import (
    CoachSession,
    Interval,
    compute_bookable_slots,
    is_penalty_free,
    is_within_makeup_window,
    makeup_window_end,
    slice_into_slots,
)

_TUE = date(2026, 6, 9)  # a Tuesday
CAL = {
    "timezone": "Africa/Lagos",
    "recurring": [{"weekday": "tue", "start": "06:00", "end": "10:00"}],
    "slot_minutes": 60,
}


def _utc(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=timezone.utc)


def test_expands_recurring_block_into_slots():
    slots = compute_bookable_slots(CAL, window_start=_TUE, window_end=_TUE)
    assert len(slots) == 4  # 06:00–10:00 Lagos = 4 × 60-min
    assert slots[0].start == _utc(2026, 6, 9, 5)  # Lagos 06:00 == 05:00 UTC
    assert slots[0].end == _utc(2026, 6, 9, 6)
    assert all(s.ok for s in slots)


def test_non_matching_weekday_yields_nothing():
    monday = date(2026, 6, 8)
    assert compute_bookable_slots(CAL, window_start=monday, window_end=monday) == []


def _session(start_h, end_h, *, capacity=10, booked=0, sid="s1", title="Club"):
    return CoachSession(
        start=_utc(2026, 6, 9, start_h),
        end=_utc(2026, 6, 9, end_h),
        session_id=sid,
        title=title,
        capacity=capacity,
        booked_count=booked,
    )


def test_full_session_blocks_open_slot_and_is_not_joinable():
    full = _session(6, 7, capacity=10, booked=10)  # 07:00–08:00 Lagos, no room
    slots = compute_bookable_slots(
        CAL, window_start=_TUE, window_end=_TUE, coach_sessions=[full]
    )
    assert [s for s in slots if s.kind == "join_session"] == []
    open_slots = [s for s in slots if s.kind == "open"]
    assert len(open_slots) == 3  # the overlapped 06:00–07:00 UTC gap is gone
    assert all(s.start != _utc(2026, 6, 9, 6) for s in open_slots)


def test_session_with_room_is_joinable():
    joinable = _session(6, 7, capacity=10, booked=4)  # 6 spots left
    slots = compute_bookable_slots(
        CAL, window_start=_TUE, window_end=_TUE, coach_sessions=[joinable]
    )
    joins = [s for s in slots if s.kind == "join_session"]
    assert len(joins) == 1
    assert joins[0].session_id == "s1"
    assert joins[0].spots_left == 6
    # the session still blocks the overlapping open slot
    assert len([s for s in slots if s.kind == "open"]) == 3


def test_joinable_session_returned_without_published_availability():
    joinable = _session(6, 7, booked=0)
    slots = compute_bookable_slots(
        {}, window_start=_TUE, window_end=_TUE, coach_sessions=[joinable]
    )
    assert [s.kind for s in slots] == ["join_session"]


def test_blackout_removes_day():
    cal = {**CAL, "blackouts": [{"start": "2026-06-09", "end": "2026-06-09"}]}
    assert compute_bookable_slots(cal, window_start=_TUE, window_end=_TUE) == []


def test_spacing_within_min_hours_flagged():
    learner = [_utc(2026, 6, 9, 8)]  # 09:00 Lagos, same day
    slots = compute_bookable_slots(
        CAL, window_start=_TUE, window_end=_TUE, learner_sessions=learner
    )
    assert all(not s.ok for s in slots)  # all within the 48h default
    assert any("min 48h" in w for s in slots for w in s.warnings)


def test_back_to_back_day_flagged_beyond_min_hours():
    learner = [_utc(2026, 6, 10, 21)]  # next calendar day, well beyond 1h
    slots = compute_bookable_slots(
        CAL,
        window_start=_TUE,
        window_end=_TUE,
        learner_sessions=learner,
        min_hours_between=1,
    )
    assert any("back-to-back" in w for s in slots for w in s.warnings)


def test_min_hours_override_relaxes_spacing():
    learner = [_utc(2026, 6, 9, 8)]
    slots = compute_bookable_slots(
        CAL,
        window_start=_TUE,
        window_end=_TUE,
        learner_sessions=learner,
        min_hours_between=1,
    )
    assert slots[0].ok  # 05:00 UTC slot is 3h away → fine under 1h spacing


def test_slice_into_slots_with_buffer():
    block = Interval(_utc(2026, 6, 9, 5), _utc(2026, 6, 9, 8))  # 3h
    slots = slice_into_slots(block, slot_minutes=60, buffer_minutes=30)
    assert len(slots) == 2  # 05:00–06:00, 06:30–07:30 (next would run past 08:00)


def test_empty_calendar_yields_nothing():
    assert compute_bookable_slots({}, window_start=_TUE, window_end=_TUE) == []


def test_penalty_free_threshold():
    now = _utc(2026, 6, 9, 8)
    assert is_penalty_free(now, _utc(2026, 6, 11, 8)) is True  # 48h notice
    assert is_penalty_free(now, _utc(2026, 6, 9, 10)) is False  # 2h notice


def test_makeup_window():
    assert makeup_window_end(date(2026, 6, 1)) == date(2026, 6, 15)  # missed + 14d
    # block ending earlier than +14d wins
    assert makeup_window_end(date(2026, 6, 1), block_end=date(2026, 6, 10)) == date(
        2026, 6, 10
    )
    assert is_within_makeup_window(date(2026, 6, 10), date(2026, 6, 1)) is True
    assert is_within_makeup_window(date(2026, 6, 20), date(2026, 6, 1)) is False
