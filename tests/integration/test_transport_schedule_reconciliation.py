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


@pytest.mark.asyncio
@pytest.mark.integration
async def test_template_replay_preserves_existing_ride_identity_and_terms(
    transport_client, db_session
):
    from sqlalchemy import select

    suffix = uuid.uuid4().hex[:8]
    areas = [
        RideArea(name=f"Area {n} {suffix}", slug=f"area-{n}-{suffix}") for n in (1, 2)
    ]
    db_session.add_all(areas)
    await db_session.flush()
    session_id = uuid.uuid4()
    existing = SessionRideConfig(
        session_id=session_id, ride_area_id=areas[0].id, cost=500000, capacity=4
    )
    db_session.add(existing)
    await db_session.commit()
    original_id = existing.id
    path = (
        f"/internal/transport/sessions/{session_id}/ride-configs?preserve_existing=true"
    )
    payload = [
        {"ride_area_id": str(area.id), "cost": 7000, "capacity": 8} for area in areas
    ]
    first = await transport_client.post(path, json=payload)
    replay = await transport_client.post(path, json=payload)
    assert first.status_code == replay.status_code == 200
    assert first.json()["created"] == 1 and replay.json()["created"] == 0
    rows = (
        (
            await db_session.execute(
                select(SessionRideConfig).where(
                    SessionRideConfig.session_id == session_id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 2
    await db_session.refresh(existing)
    assert (existing.id, existing.cost, existing.capacity) == (original_id, 500000, 4)
