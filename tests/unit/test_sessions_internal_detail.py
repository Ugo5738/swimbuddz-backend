from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from services.sessions_service.routers import internal


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _RowsResult:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class _ScalarsResult:
    def __init__(self, values):
        self.values = values

    def scalars(self):
        return self

    def all(self):
        return self.values


@pytest.mark.asyncio
async def test_internal_session_detail_includes_booking_and_coach_context():
    now = datetime.now(timezone.utc)
    session = SimpleNamespace(
        id=uuid4(),
        title="Orca Technique",
        description="Freestyle pacing",
        notes=None,
        session_type=SimpleNamespace(value="club"),
        status=SimpleNamespace(value="scheduled"),
        starts_at=now + timedelta(days=2),
        ends_at=now + timedelta(days=2, hours=2),
        pool_id=uuid4(),
        location_name="Rowe Park Pool",
        location_address=None,
        location=SimpleNamespace(value="rowe_park_pool"),
        cohort_id=None,
        pod_id=uuid4(),
        capacity=20,
        pool_fee=500000,
        ride_share_fee=0,
        week_number=None,
        lesson_title=None,
        timezone="Africa/Lagos",
    )
    member_id = uuid4()
    coach_id = uuid4()
    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                _ScalarResult(session),
                _RowsResult([(member_id, 2)]),
                _ScalarsResult([coach_id]),
            ]
        )
    )

    result = await internal.get_session_by_id(
        session.id,
        _=SimpleNamespace(),
        db=db,
    )

    assert result.description == "Freestyle pacing"
    assert result.occupied_slots == 2
    assert result.confirmed_booking_member_ids == [str(member_id)]
    assert result.coach_member_ids == [str(coach_id)]
