"""Ride departures follow an Event Session reschedule without losing offsets."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from services.transport_service.models import RideArea, SessionRideConfig


@pytest.mark.asyncio
@pytest.mark.integration
async def test_explicit_ride_departure_shifts_with_session_start(
    transport_client, db_session
):
    suffix = uuid.uuid4().hex[:8]
    area = RideArea(name=f"Schedule area {suffix}", slug=f"schedule-area-{suffix}")
    db_session.add(area)
    await db_session.flush()

    session_id = uuid.uuid4()
    old_start = datetime(2026, 10, 3, 9, tzinfo=timezone.utc)
    explicit = SessionRideConfig(
        session_id=session_id,
        ride_area_id=area.id,
        cost=0,
        capacity=4,
        departure_time=old_start - timedelta(hours=2),
    )
    derived = SessionRideConfig(
        session_id=session_id,
        ride_area_id=area.id,
        cost=0,
        capacity=4,
        departure_time=None,
    )
    db_session.add_all([explicit, derived])
    await db_session.commit()

    new_start = old_start + timedelta(days=1, hours=1)
    response = await transport_client.patch(
        f"/internal/transport/sessions/{session_id}/schedule",
        json={
            "old_starts_at": old_start.isoformat(),
            "new_starts_at": new_start.isoformat(),
        },
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"updated": 1}
    await db_session.refresh(explicit)
    await db_session.refresh(derived)
    assert explicit.departure_time == new_start - timedelta(hours=2)
    assert derived.departure_time is None
