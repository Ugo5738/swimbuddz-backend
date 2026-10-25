"""Regular weekly attendance counts separately from paid extra sessions."""
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from libs.common.datetime_utils import utc_now
from services.academy_service.routers.enrollments import class_entitlements

pytestmark = pytest.mark.asyncio


class Response:
    def __init__(self, rows):
        self.rows = rows

    def raise_for_status(self):
        return None

    def json(self):
        return self.rows


async def test_regular_present_and_late_count_but_extra_does_not(monkeypatch):
    now = utc_now()
    cohort_id, member_id = uuid4(), uuid4()
    ids = [str(uuid4()) for _ in range(4)]
    rows = [
        {
            "id": sid,
            "session_type": "cohort_class",
            "cohort_fee_mode": "paid_extra" if number == 2 else "included",
            "status": "scheduled",
            "starts_at": (now - timedelta(days=4 - number)).isoformat(),
        }
        for number, sid in enumerate(ids)
    ]
    attendance = [
        {"session_id": ids[0], "status": "present"},
        {"session_id": ids[1], "status": "late"},
        {"session_id": ids[2], "status": "present"},
    ]
    get_sessions = AsyncMock(return_value=Response(rows))
    get_attendance = AsyncMock(return_value=attendance)
    monkeypatch.setattr(class_entitlements, "internal_get", get_sessions)
    monkeypatch.setattr(class_entitlements, "get_member_attendance", get_attendance)
    enrollment = SimpleNamespace(
        cohort_id=cohort_id,
        member_id=member_id,
        enrolled_at=now - timedelta(weeks=2),
    )
    plan = SimpleNamespace(opening_as_of=now - timedelta(weeks=1))
    count, unverified = await class_entitlements._routine_consumption(
        enrollment, plan, []
    )
    assert count == 2
    assert unverified == [ids[3]]
    assert ids[2] not in get_attendance.await_args.kwargs["session_ids"]


async def test_manual_paid_allocation_excludes_included_session_from_routine(monkeypatch):
    now = utc_now()
    sid = str(uuid4())
    monkeypatch.setattr(
        class_entitlements,
        "internal_get",
        AsyncMock(
            return_value=Response(
                [
                    {
                        "id": sid,
                        "session_type": "cohort_class",
                        "cohort_fee_mode": "included",
                        "status": "completed",
                        "starts_at": (now - timedelta(days=1)).isoformat(),
                    }
                ]
            )
        ),
    )
    getter = AsyncMock(return_value=[])
    monkeypatch.setattr(class_entitlements, "get_member_attendance", getter)
    enrollment = SimpleNamespace(
        cohort_id=uuid4(),
        member_id=uuid4(),
        enrolled_at=now - timedelta(days=3),
    )
    plan = SimpleNamespace(opening_as_of=now - timedelta(days=2))
    allocation = SimpleNamespace(session_id=uuid4())
    allocation.session_id = uuid4()
    allocation = SimpleNamespace(session_id=__import__("uuid").UUID(sid))
    count, unverified = await class_entitlements._routine_consumption(
        enrollment, plan, [allocation]
    )
    assert (count, unverified) == (0, [])
    getter.assert_not_awaited()
