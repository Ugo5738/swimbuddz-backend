"""Unit tests for academy perfect-attendance rewards on cohort completion.

`_emit_graduation_rewards` (academy_service) judges perfect attendance
against the cohort's COMPLETED sessions (sessions_service) and the member's
stored attendance records (attendance_service), both fetched via internal
HTTP. Key decision under test: unmarked sessions have NO stored attendance
record (the UI merely displays them as "Absent") and count as missed —
only a stored 'present'/'late' record on every completed session qualifies.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from services.academy_service.tasks.enrollment import (
    _attended_all_sessions,
    _emit_graduation_rewards,
)

SESSION_1 = str(uuid.uuid4())
SESSION_2 = str(uuid.uuid4())


def _record(session_id: str, status: str) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "session_id": session_id,
        "member_id": str(uuid.uuid4()),
        "status": status,
    }


@pytest.mark.unit
class TestAttendedAllSessions:
    """Pure qualification logic."""

    def test_present_at_every_session_qualifies(self):
        records = [_record(SESSION_1, "present"), _record(SESSION_2, "present")]
        assert _attended_all_sessions(records, [SESSION_1, SESSION_2]) is True

    def test_late_counts_as_attended(self):
        records = [_record(SESSION_1, "present"), _record(SESSION_2, "late")]
        assert _attended_all_sessions(records, [SESSION_1, SESSION_2]) is True

    def test_unmarked_session_counts_as_missed(self):
        # No stored record for SESSION_2 — displays as "Absent" in the UI
        # but has no row. Must disqualify.
        records = [_record(SESSION_1, "present")]
        assert _attended_all_sessions(records, [SESSION_1, SESSION_2]) is False

    def test_absent_record_disqualifies(self):
        records = [_record(SESSION_1, "present"), _record(SESSION_2, "absent")]
        assert _attended_all_sessions(records, [SESSION_1, SESSION_2]) is False

    def test_excused_record_disqualifies(self):
        records = [_record(SESSION_1, "present"), _record(SESSION_2, "excused")]
        assert _attended_all_sessions(records, [SESSION_1, SESSION_2]) is False

    def test_no_completed_sessions_never_qualifies(self):
        # Guard against vacuous truth: a cohort with zero completed
        # sessions must not shower everyone with perfect attendance.
        assert _attended_all_sessions([], []) is False


# ---------------------------------------------------------------------------
# _emit_graduation_rewards orchestration
# ---------------------------------------------------------------------------

MEMBER_ID = uuid.uuid4()
ENROLLMENT_ID = uuid.uuid4()
COHORT_ID = uuid.uuid4()
AUTH_ID = "auth-user-1"


def _make_cohort() -> SimpleNamespace:
    return SimpleNamespace(
        id=COHORT_ID,
        name="Cohort Alpha",
        program=SimpleNamespace(name="Learn to Swim"),
        end_date=None,
    )


def _make_db_with_enrollments(enrollments: list) -> Mock:
    result = Mock()
    result.scalars.return_value.all.return_value = enrollments
    db = Mock()
    db.execute = AsyncMock(return_value=result)
    return db


def _patches(*, completed_session_ids, attendance):
    """Patch every cross-service call where enrollment.py looks it up."""
    base = "services.academy_service.tasks.enrollment"
    settings = Mock(
        MEMBERS_SERVICE_URL="http://members", POST_ACADEMY_FREE_CLUB_MONTHS=1
    )
    return {
        "get_members_bulk": patch(
            f"{base}.get_members_bulk",
            new=AsyncMock(return_value=[{"id": str(MEMBER_ID), "auth_id": AUTH_ID}]),
        ),
        "emit_rewards_event": patch(
            f"{base}.emit_rewards_event", new=AsyncMock(return_value={"accepted": True})
        ),
        "internal_post": patch(f"{base}.internal_post", new=AsyncMock()),
        "get_settings": patch(f"{base}.get_settings", new=Mock(return_value=settings)),
        "get_completed_session_ids_for_cohort": patch(
            f"{base}.get_completed_session_ids_for_cohort",
            new=(
                completed_session_ids
                if isinstance(completed_session_ids, AsyncMock)
                else AsyncMock(return_value=completed_session_ids)
            ),
        ),
        "get_member_attendance": patch(
            f"{base}.get_member_attendance",
            new=(
                attendance
                if isinstance(attendance, AsyncMock)
                else AsyncMock(return_value=attendance)
            ),
        ),
    }


async def _run(*, completed_session_ids, attendance):
    """Run _emit_graduation_rewards with mocks; return the started mocks."""
    enrollment = SimpleNamespace(id=ENROLLMENT_ID, member_id=MEMBER_ID)
    db = _make_db_with_enrollments([enrollment])
    patches = _patches(
        completed_session_ids=completed_session_ids, attendance=attendance
    )
    started = {}
    try:
        for name, p in patches.items():
            started[name] = p.start()
        await _emit_graduation_rewards(db, _make_cohort())
    finally:
        patch.stopall()
    return started


def _events_of_type(emit_mock: AsyncMock, event_type: str) -> list:
    return [
        c.kwargs
        for c in emit_mock.await_args_list
        if c.kwargs.get("event_type") == event_type
    ]


@pytest.mark.unit
class TestEmitGraduationRewards:
    async def test_perfect_attendance_emitted_when_present_at_all_sessions(self):
        mocks = await _run(
            completed_session_ids=[SESSION_1, SESSION_2],
            attendance=[
                _record(SESSION_1, "present"),
                _record(SESSION_2, "late"),
            ],
        )

        graduated = _events_of_type(mocks["emit_rewards_event"], "academy.graduated")
        perfect = _events_of_type(
            mocks["emit_rewards_event"], "academy.perfect_attendance"
        )
        assert len(graduated) == 1
        assert len(perfect) == 1

        event = perfect[0]
        assert event["member_auth_id"] == AUTH_ID
        assert event["member_id"] == str(MEMBER_ID)
        assert event["idempotency_key"] == (
            f"academy-perfect-attendance-{ENROLLMENT_ID}"
        )
        # The reward template interpolates {program_name} and {cohort_name}
        assert event["event_data"]["program_name"] == "Learn to Swim"
        assert event["event_data"]["cohort_name"] == "Cohort Alpha"
        assert event["event_data"]["cohort_id"] == str(COHORT_ID)
        assert event["event_data"]["sessions_attended"] == 2

        # Attendance was filtered to the cohort's completed sessions
        att_kwargs = mocks["get_member_attendance"].await_args
        assert att_kwargs.args[0] == str(MEMBER_ID)
        assert att_kwargs.kwargs["session_ids"] == [SESSION_1, SESSION_2]

    async def test_unmarked_session_does_not_earn_perfect_attendance(self):
        mocks = await _run(
            completed_session_ids=[SESSION_1, SESSION_2],
            attendance=[_record(SESSION_1, "present")],  # SESSION_2 unmarked
        )

        assert _events_of_type(mocks["emit_rewards_event"], "academy.graduated")
        assert not _events_of_type(
            mocks["emit_rewards_event"], "academy.perfect_attendance"
        )

    async def test_no_completed_sessions_skips_perfect_attendance(self):
        mocks = await _run(completed_session_ids=[], attendance=[])

        assert _events_of_type(mocks["emit_rewards_event"], "academy.graduated")
        assert not _events_of_type(
            mocks["emit_rewards_event"], "academy.perfect_attendance"
        )
        # No sessions to judge against — attendance shouldn't even be fetched
        mocks["get_member_attendance"].assert_not_awaited()

    async def test_attendance_fetch_failure_does_not_block_graduation(self):
        mocks = await _run(
            completed_session_ids=[SESSION_1],
            attendance=AsyncMock(side_effect=RuntimeError("attendance down")),
        )

        assert _events_of_type(mocks["emit_rewards_event"], "academy.graduated")
        assert not _events_of_type(
            mocks["emit_rewards_event"], "academy.perfect_attendance"
        )
        # Club bridge still granted despite the attendance failure
        mocks["internal_post"].assert_awaited_once()

    async def test_sessions_fetch_failure_does_not_block_graduation(self):
        mocks = await _run(
            completed_session_ids=AsyncMock(side_effect=RuntimeError("sessions down")),
            attendance=[],
        )

        assert _events_of_type(mocks["emit_rewards_event"], "academy.graduated")
        assert not _events_of_type(
            mocks["emit_rewards_event"], "academy.perfect_attendance"
        )
        mocks["get_member_attendance"].assert_not_awaited()
