"""Student exceptions use enrolment ownership, not a separate booking."""

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from services.attendance_service.models import AttendanceRecord, AttendanceStatus
from services.attendance_service.routers.member import academy
from services.attendance_service.schemas.academy import (
    AcademyAttendanceExceptionRequest,
)
from services.attendance_service.services.academy_defaults import reconcile_session

NOW = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)


@pytest.fixture
def context(monkeypatch):
    member = SimpleNamespace(id=uuid.uuid4())
    swim = {
        "id": str(uuid.uuid4()),
        "cohort_id": str(uuid.uuid4()),
        "session_type": "cohort_class",
        "status": "scheduled",
        "cohort_fee_mode": "included",
        "starts_at": (NOW + timedelta(days=2)).isoformat(),
        "ends_at": (NOW + timedelta(days=2, hours=1)).isoformat(),
    }
    enrollment = {"enrolled": True, "status": "enrolled", "access_suspended": False}
    lookup = AsyncMock(return_value=enrollment)
    monkeypatch.setattr(academy, "utc_now", lambda: NOW)
    monkeypatch.setattr(academy, "get_session_by_id", AsyncMock(return_value=swim))
    monkeypatch.setattr(academy, "check_cohort_enrollment", lookup)
    monkeypatch.setattr(academy, "require_academy_safety_clearance", AsyncMock())
    return SimpleNamespace(
        member=member, swim=swim, enrollment=enrollment, lookup=lookup
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_student_without_booking_reports_own_absence_and_default_preserves_it(
    db_session, context
):
    row = await academy.report_my_academy_attendance(
        uuid.UUID(context.swim["id"]),
        AcademyAttendanceExceptionRequest(status="excused", notes="Away"),
        current_member=context.member,
        db=db_session,
    )
    assert row.member_id == context.member.id and row.status == AttendanceStatus.EXCUSED
    context.lookup.assert_awaited_once_with(
        context.swim["cohort_id"], str(context.member.id), calling_service="attendance"
    )
    assert row.booking_id is None
    students = [
        {
            "member_id": str(context.member.id),
            "status": "enrolled",
            "created_at": NOW.isoformat(),
        }
    ]
    assert (
        await reconcile_session(
            db_session,
            context.swim,
            students,
            cleared_member_ids={str(context.member.id)},
            now=NOW + timedelta(days=3),
        )
    )["created"] == 0
    assert row.status == AttendanceStatus.EXCUSED


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [{"enrolled": False}, {"access_suspended": True}, {"paused_at": NOW.isoformat()}],
)
async def test_student_cannot_change_another_cohort_or_inactive_enrollment(
    db_session, context, change
):
    context.enrollment.update(change)
    with pytest.raises(HTTPException) as raised:
        await academy.report_my_academy_attendance(
            uuid.UUID(context.swim["id"]),
            AcademyAttendanceExceptionRequest(status="absent"),
            current_member=context.member,
            db=db_session,
        )
    assert raised.value.status_code == 403


@pytest.mark.integration
@pytest.mark.asyncio
async def test_student_can_undo_own_report_but_cannot_clear_coach_exception(
    db_session, context
):
    sid = uuid.UUID(context.swim["id"])
    await academy.report_my_academy_attendance(
        sid,
        AcademyAttendanceExceptionRequest(status="late"),
        current_member=context.member,
        db=db_session,
    )
    restored = await academy.report_my_academy_attendance(
        sid,
        AcademyAttendanceExceptionRequest(status="present"),
        current_member=context.member,
        db=db_session,
    )
    assert restored.status == AttendanceStatus.PRESENT
    restored.status = AttendanceStatus.ABSENT
    restored.notes = "Coach recorded absence"
    await db_session.commit()
    with pytest.raises(HTTPException) as raised:
        await academy.report_my_academy_attendance(
            sid,
            AcademyAttendanceExceptionRequest(status="present"),
            current_member=context.member,
            db=db_session,
        )
    assert raised.value.status_code == 409
    row = (
        await db_session.scalars(
            select(AttendanceRecord).where(AttendanceRecord.session_id == sid)
        )
    ).one()
    assert (
        row.status == AttendanceStatus.ABSENT and row.notes == "Coach recorded absence"
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_short_notice_absence_is_not_misclassified_as_excused(
    db_session, context
):
    context.swim["starts_at"] = (NOW + timedelta(hours=2)).isoformat()
    with pytest.raises(HTTPException) as raised:
        await academy.report_my_academy_attendance(
            uuid.UUID(context.swim["id"]),
            AcademyAttendanceExceptionRequest(status="excused"),
            current_member=context.member,
            db=db_session,
        )
    assert raised.value.status_code == 422
    row = await academy.report_my_academy_attendance(
        uuid.UUID(context.swim["id"]),
        AcademyAttendanceExceptionRequest(status="absent"),
        current_member=context.member,
        db=db_session,
    )
    assert row.status == AttendanceStatus.ABSENT


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"cohort_fee_mode": "paid_extra"},
        {"session_type": "club"},
        {"status": "cancelled"},
        {"ends_at": NOW.isoformat()},
    ],
)
async def test_student_route_rejects_unavailable_or_nonincluded_classes(
    db_session, context, change
):
    context.swim.update(change)
    with pytest.raises(HTTPException) as raised:
        await academy.report_my_academy_attendance(
            uuid.UUID(context.swim["id"]),
            AcademyAttendanceExceptionRequest(status="absent"),
            current_member=context.member,
            db=db_session,
        )
    assert raised.value.status_code == 422


@pytest.mark.integration
@pytest.mark.asyncio
async def test_student_cannot_restore_present_on_unassigned_extra(db_session, context):
    context.swim["cohort_attendance_mode"] = "individual"
    with pytest.raises(HTTPException) as raised:
        await academy.report_my_academy_attendance(
            uuid.UUID(context.swim["id"]),
            AcademyAttendanceExceptionRequest(status="present"),
            current_member=context.member,
            db=db_session,
        )
    assert raised.value.status_code == 422
    context.lookup.assert_not_awaited()
