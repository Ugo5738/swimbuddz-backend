"""Academy defaults never assume future, optional or out-of-enrolment attendance."""

from datetime import datetime, timezone

import pytest

from services.attendance_service.services.academy_defaults import eligible_for_default

NOW = datetime(2026, 10, 19, tzinfo=timezone.utc)
SESSION = {
    "session_type": "cohort_class",
    "status": "scheduled",
    "cohort_fee_mode": "included",
    "starts_at": "2026-10-18T15:00:00+00:00",
}
STUDENT = {"status": "enrolled", "created_at": "2026-10-07T09:00:00+00:00"}


def test_included_class_needs_no_booking():
    assert eligible_for_default(SESSION, STUDENT, now=NOW)


def test_student_exception_payload_cannot_supply_another_member_identity():
    from pydantic import ValidationError
    from services.attendance_service.schemas.academy import (
        AcademyAttendanceExceptionRequest,
    )

    with pytest.raises(ValidationError):
        AcademyAttendanceExceptionRequest(status="absent", member_id="another-student")


@pytest.mark.parametrize(
    "change",
    [
        {"starts_at": "2026-10-25T15:00:00+00:00"},
        {"status": "cancelled"},
        {"status": "draft"},
        {"session_type": "club"},
        {"cohort_fee_mode": "paid_extra"},
        {"cohort_attendance_mode": "individual"},
    ],
)
def test_does_not_create_unearned_attendance(change):
    assert not eligible_for_default({**SESSION, **change}, STUDENT, now=NOW)


@pytest.mark.parametrize(
    "change",
    [
        {"status": "waitlist"},
        {"status": "pending_approval"},
        {"status": "dropped"},
        {"access_suspended": True},
        {"paused_at": "2026-10-17T12:00:00+00:00"},
        {"dropped_at": "2026-10-18T15:00:00+00:00"},
        {"enrolled_at": "2026-10-19T12:00:00+00:00"},
        {"created_at": None},
    ],
)
def test_excludes_inactive_students_and_classes_before_joining(change):
    assert not eligible_for_default(SESSION, {**STUDENT, **change}, now=NOW)


def test_history_before_pause_is_still_eligible():
    assert eligible_for_default(
        SESSION, {**STUDENT, "paused_at": "2026-10-19T00:00:00+00:00"}, now=NOW
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "session_type,fee_mode,created",
    [
        ("cohort_class", "included", 0),
        ("cohort_class", "paid_extra", 1),
        ("club", "included", 1),
    ],
)
async def test_legacy_sweep_does_not_invent_included_academy_absence(
    monkeypatch, session_type, fee_mode, created
):
    import uuid
    from unittest.mock import AsyncMock, MagicMock
    from services.attendance_service import tasks

    db = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=db)
    context.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(tasks, "AsyncSessionLocal", lambda: context)
    monkeypatch.setattr(tasks, "utc_now", lambda: NOW)
    monkeypatch.setattr(
        tasks,
        "list_confirmed_bookings_since",
        AsyncMock(
            return_value=[
                {
                    "id": str(uuid.uuid4()),
                    "member_id": str(uuid.uuid4()),
                    "session_id": str(uuid.uuid4()),
                },
            ]
        ),
    )
    monkeypatch.setattr(
        tasks,
        "get_session_by_id",
        AsyncMock(
            return_value={
                "session_type": session_type,
                "cohort_fee_mode": fee_mode,
                "ends_at": "2026-10-18T16:00:00+00:00",
            }
        ),
    )
    assert (await tasks.sweep_no_show_bookings())["created"] == created
    assert db.add.call_count == created
