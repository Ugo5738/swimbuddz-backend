from services.reporting_service.services import aggregator


def _session(session_id: str) -> dict:
    return {
        "id": session_id,
        "session_type": "club",
        "starts_at": "2026-04-07T08:00:00+00:00",
        "ends_at": "2026-04-07T10:00:00+00:00",
        "location_name": "Test Pool",
    }


def test_expected_session_summary_counts_reportable_outcomes():
    expected = {
        "present-session": _session("present-session"),
        "late-session": _session("late-session"),
        "excused-session": _session("excused-session"),
        "missing-session": _session("missing-session"),
    }
    status_by_session = aggregator._attendance_status_by_session(
        [
            {"session_id": "present-session", "status": "present"},
            {"session_id": "late-session", "status": "late"},
            {"session_id": "excused-session", "status": "excused"},
        ]
    )

    summary = aggregator._summarize_expected_sessions(
        expected_sessions=expected,
        status_by_session=status_by_session,
    )

    assert summary["total_attended"] == 2
    assert summary["total_available"] == 3
    assert summary["total_absent"] == 1
    assert summary["total_excused"] == 1
    assert summary["attendance_rate"] == 2 / 3
