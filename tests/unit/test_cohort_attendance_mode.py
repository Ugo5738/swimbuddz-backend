"""Extra Academy sessions need an individual attendance and class decision."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from services.sessions_service.schemas.main import SessionCreate
from services.attendance_service.routers.member import coach_mark
from services.attendance_service.schemas import CoachAttendanceMarkRequest


def session_payload(**overrides):
    now = datetime.now(timezone.utc)
    return {
        "title": "Individual lesson",
        "session_type": "cohort_class",
        "cohort_id": uuid4(),
        "starts_at": now,
        "ends_at": now + timedelta(hours=1),
        **overrides,
    }


def test_regular_and_prepaid_extra_have_independent_attendance_settings():
    regular = SessionCreate(**session_payload())
    extra = SessionCreate(**session_payload(cohort_attendance_mode="individual"))
    assert regular.cohort_attendance_mode == "automatic"
    assert extra.cohort_attendance_mode == "individual"
    assert regular.cohort_fee_mode == extra.cohort_fee_mode == "included"


def test_paid_extra_cannot_default_the_cohort_present():
    extra = SessionCreate(**session_payload(cohort_fee_mode="paid_extra"))
    assert extra.cohort_attendance_mode == "individual"


def test_other_session_types_cannot_use_academy_individual_attendance():
    with pytest.raises(ValidationError, match="Only Academy classes"):
        SessionCreate(
            **session_payload(
                session_type="community",
                cohort_id=None,
                cohort_attendance_mode="individual",
            )
        )


@pytest.mark.asyncio
async def test_coach_cannot_bypass_extra_class_allocation(monkeypatch):
    sid, mid = uuid4(), uuid4()
    monkeypatch.setattr(coach_mark, "require_admin_or_coach_for_session", AsyncMock())
    monkeypatch.setattr(
        coach_mark,
        "get_session_by_id",
        AsyncMock(
            return_value={
                "session_type": "cohort_class",
                "cohort_attendance_mode": "individual",
                "cohort_fee_mode": "included",
            }
        ),
    )
    monkeypatch.setattr(
        coach_mark, "get_confirmed_booking_member_ids", AsyncMock(return_value=[])
    )
    db = SimpleNamespace(commit=AsyncMock(), execute=AsyncMock())
    with pytest.raises(HTTPException) as caught:
        await coach_mark.coach_mark_session_attendance(
            sid,
            CoachAttendanceMarkRequest(
                entries=[{"member_id": mid, "status": "present"}]
            ),
            SimpleNamespace(),
            db,
        )
    assert caught.value.status_code == 422
    assert "Academy student participation" in caught.value.detail
    db.execute.assert_not_awaited()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_regular_extension_ignores_extra_and_cancelled_weekdays():
    from unittest.mock import Mock
    from services.sessions_service.models import Session
    from services.sessions_service.services.cohort_sessions import (
        generate_sessions_for_cohort,
    )

    cohort = uuid4()

    def swim(day, **settings):
        start = datetime(2026, 12, day, 13, tzinfo=timezone.utc)
        return Session(
            title="Week 12 - Freestyle",
            session_type="cohort_class",
            cohort_id=cohort,
            starts_at=start,
            ends_at=start + timedelta(hours=1),
            timezone="Africa/Lagos",
            week_number=12,
            capacity=3,
            pool_fee=0,
            ride_share_fee=0,
            allows_guests=False,
            max_guests_per_booking=0,
            cohort_attendance_mode="automatic",
            cohort_fee_mode="included",
            **settings,
        )

    regular = swim(13, status="scheduled")
    cancelled_friday = swim(18, status="cancelled")
    extra = swim(19, status="scheduled")
    extra.cohort_attendance_mode, extra.week_number = "individual", 99
    db = SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(
                scalars=lambda: SimpleNamespace(
                    all=lambda: [extra, cancelled_friday, regular]
                )
            )
        ),
        add=Mock(),
        flush=AsyncMock(),
    )
    result = await generate_sessions_for_cohort(
        db,
        cohort,
        datetime(2026, 12, 20, 15, tzinfo=timezone.utc),
        datetime(2026, 12, 28, tzinfo=timezone.utc),
    )
    assert result["created"] == 1
    created = db.add.call_args.args[0]
    assert created.starts_at == datetime(2026, 12, 27, 13, tzinfo=timezone.utc)
    assert created.cohort_attendance_mode == "automatic" and created.week_number == 13
