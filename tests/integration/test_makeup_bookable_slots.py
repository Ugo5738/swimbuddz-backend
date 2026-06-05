"""Integration tests for the make-up bookable-slots endpoint + MakeupBooking model.

The endpoint fetches coach availability over HTTP; we monkeypatch that fetch
(``get_coach_availability``) in the router module so no real members_service
call is made. ``sessions_client`` is admin-authed by the fixture wiring.
"""

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

import services.sessions_service.routers.makeups as makeups_mod
from tests.factories import SessionCoachFactory, SessionFactory

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

_CAL = {
    "availability_calendar": {
        "timezone": "Africa/Lagos",
        "recurring": [{"weekday": "tue", "start": "06:00", "end": "10:00"}],
        "slot_minutes": 60,
    },
    "min_hours_between_sessions": None,
}
_PARAMS = {"from": "2026-06-09", "to": "2026-06-09"}  # 2026-06-09 is a Tuesday


def _patch_availability(monkeypatch, value):
    async def _fake(member_id, *, calling_service):
        return value

    monkeypatch.setattr(makeups_mod, "get_coach_availability", _fake)


async def test_availability_not_set(sessions_client, monkeypatch):
    _patch_availability(monkeypatch, None)
    params = {"coach_id": str(uuid.uuid4()), "learner_id": str(uuid.uuid4()), **_PARAMS}
    r = await sessions_client.get("/makeups/bookable-slots", params=params)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["availability_set"] is False
    assert body["slots"] == []


async def test_returns_slots(sessions_client, monkeypatch):
    _patch_availability(monkeypatch, _CAL)
    params = {"coach_id": str(uuid.uuid4()), "learner_id": str(uuid.uuid4()), **_PARAMS}
    r = await sessions_client.get("/makeups/bookable-slots", params=params)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["availability_set"] is True
    assert len(body["slots"]) == 4
    assert all(s["ok"] for s in body["slots"])
    assert all(s["kind"] == "open" for s in body["slots"])


async def test_existing_session_is_joinable(sessions_client, db_session, monkeypatch):
    _patch_availability(monkeypatch, _CAL)
    coach_id = uuid.uuid4()
    # Coach runs a session 07:00–08:00 Lagos (06:00–07:00 UTC) with room.
    sess = SessionFactory.create(
        starts_at=datetime(2026, 6, 9, 6, 0, tzinfo=timezone.utc),
        ends_at=datetime(2026, 6, 9, 7, 0, tzinfo=timezone.utc),
        status="SCHEDULED",
        capacity=10,
    )
    db_session.add(sess)
    await db_session.flush()
    db_session.add(SessionCoachFactory.create(session_id=sess.id, coach_id=coach_id))
    await db_session.commit()

    params = {"coach_id": str(coach_id), "learner_id": str(uuid.uuid4()), **_PARAMS}
    r = await sessions_client.get("/makeups/bookable-slots", params=params)
    assert r.status_code == 200, r.text
    slots = r.json()["slots"]
    opens = [s for s in slots if s["kind"] == "open"]
    joins = [s for s in slots if s["kind"] == "join_session"]
    assert len(opens) == 3  # the overlapping open gap is blocked
    assert len(joins) == 1
    assert joins[0]["session_id"] == str(sess.id)
    assert joins[0]["spots_left"] == 10


async def test_inverted_window_400(sessions_client, monkeypatch):
    _patch_availability(monkeypatch, _CAL)
    params = {
        "coach_id": str(uuid.uuid4()),
        "learner_id": str(uuid.uuid4()),
        "from": "2026-06-10",
        "to": "2026-06-09",
    }
    r = await sessions_client.get("/makeups/bookable-slots", params=params)
    assert r.status_code == 400


async def test_makeup_booking_insert_roundtrips(db_session):
    from services.sessions_service.models import (
        MakeupBooking,
        MakeupLearnerType,
        MakeupOrigin,
        MakeupStatus,
    )

    mb = MakeupBooking(
        learner_member_id=uuid.uuid4(),
        coach_member_id=uuid.uuid4(),
        learner_type=MakeupLearnerType.COHORT,
        origin=MakeupOrigin.EXCUSED_ABSENCE,
        status=MakeupStatus.REQUESTED,
    )
    db_session.add(mb)
    await db_session.commit()

    got = (
        await db_session.execute(select(MakeupBooking).where(MakeupBooking.id == mb.id))
    ).scalar_one()
    assert got.status == MakeupStatus.REQUESTED
    assert got.learner_type == MakeupLearnerType.COHORT
    assert got.used_grace is False
