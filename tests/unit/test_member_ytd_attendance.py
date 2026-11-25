import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from services.attendance_service.routers.member.lists import (
    compute_ytd_attendance_summary,
)


def test_ytd_summary_counts_all_programmes_cross_quarter_streaks_and_excludes_future():
    # Spans Q3 -> Q4 boundary (Sep 21, Sep 28, Oct 5, Oct 12 = 4-week cross-quarter streak)
    # Mixes Club, Academy, and Community sessions + 1 excused + 1 absent + 1 future + 1 cancelled + 1 volunteer
    s_sep21 = str(uuid.uuid4())
    s_sep28 = str(uuid.uuid4())
    s_oct05 = str(uuid.uuid4())
    s_oct12 = str(uuid.uuid4())
    s_oct19_excused = str(uuid.uuid4())
    s_oct26_absent = str(uuid.uuid4())
    s_future = str(uuid.uuid4())
    s_cancelled = str(uuid.uuid4())
    s_volunteer = str(uuid.uuid4())
    s_last_year = str(uuid.uuid4())

    sessions = {
        s_sep21: {
            "id": s_sep21,
            "session_type": "club",
            "status": "completed",
            "starts_at": "2026-09-21T08:00:00Z",
            "ends_at": "2026-09-21T10:00:00Z",
        },
        s_sep28: {
            "id": s_sep28,
            "session_type": "cohort_class",
            "status": "completed",
            "starts_at": "2026-09-28T08:00:00Z",
            "ends_at": "2026-09-28T09:30:00Z",
        },
        s_oct05: {
            "id": s_oct05,
            "session_type": "community",
            "status": "completed",
            "starts_at": "2026-10-05T08:00:00Z",
            "ends_at": "2026-10-05T10:00:00Z",
        },
        s_oct12: {
            "id": s_oct12,
            "session_type": "club",
            "status": "completed",
            "starts_at": "2026-10-12T08:00:00Z",
            "ends_at": "2026-10-12T09:30:00Z",
        },
        s_oct19_excused: {
            "id": s_oct19_excused,
            "session_type": "cohort_class",
            "status": "completed",
            "starts_at": "2026-10-19T08:00:00Z",
            "ends_at": "2026-10-19T09:30:00Z",
        },
        s_oct26_absent: {
            "id": s_oct26_absent,
            "session_type": "club",
            "status": "completed",
            "starts_at": "2026-10-26T08:00:00Z",
            "ends_at": "2026-10-26T10:00:00Z",
        },
        s_future: {
            "id": s_future,
            "session_type": "club",
            "status": "scheduled",
            "starts_at": "2026-12-05T08:00:00Z",
            "ends_at": "2026-12-05T10:00:00Z",
        },
        s_cancelled: {
            "id": s_cancelled,
            "session_type": "club",
            "status": "cancelled",
            "starts_at": "2026-10-03T08:00:00Z",
            "ends_at": "2026-10-03T10:00:00Z",
        },
        s_volunteer: {
            "id": s_volunteer,
            "session_type": "community",
            "status": "completed",
            "starts_at": "2026-11-02T08:00:00Z",
            "ends_at": "2026-11-02T10:00:00Z",
        },
        s_last_year: {
            "id": s_last_year,
            "session_type": "club",
            "status": "completed",
            "starts_at": "2025-12-20T08:00:00Z",
            "ends_at": "2025-12-20T10:00:00Z",
        },
    }

    records = [
        SimpleNamespace(session_id=s_sep21, role="swimmer", status="present"),
        SimpleNamespace(session_id=s_sep28, role="swimmer", status="late"),
        SimpleNamespace(session_id=s_oct05, role="swimmer", status="present"),
        SimpleNamespace(session_id=s_oct12, role="swimmer", status="present"),
        SimpleNamespace(session_id=s_oct19_excused, role="swimmer", status="excused"),
        SimpleNamespace(session_id=s_oct26_absent, role="swimmer", status="absent"),
        SimpleNamespace(session_id=s_future, role="swimmer", status="present"),
        SimpleNamespace(session_id=s_cancelled, role="swimmer", status="present"),
        SimpleNamespace(session_id=s_volunteer, role="volunteer", status="present"),
        SimpleNamespace(session_id=s_last_year, role="swimmer", status="present"),
    ]

    summary = compute_ytd_attendance_summary(
        records,
        sessions,
        year=2026,
        now=datetime(2026, 11, 25, 18, 0, tzinfo=timezone.utc),
    )

    assert summary.year == 2026
    assert summary.sessions_attended == 4
    assert summary.sessions_absent == 1
    assert summary.sessions_excused == 1
    # Excused is excluded from denominator per ADR-004: 4 / (4 + 1) = 0.8
    assert summary.sessions_recorded == 5
    assert summary.attendance_rate == 0.8
    # 2.0 + 1.5 + 2.0 + 1.5 = 7.0h
    assert summary.pool_hours == 7.0
    # Cross-quarter streak preserved (Sep 21 -> Sep 28 -> Oct 5 -> Oct 12 = 4w)
    assert summary.streak_longest == 4
    assert summary.active_weeks == 4
    assert summary.streak_current == 0
