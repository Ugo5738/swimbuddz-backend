"""Integration tests for learner self-serve make-up requests (Phase 1.5).

Learner endpoints resolve the member via ``get_member_by_auth_id`` and the admin
confirm resolves the learner via ``get_member_by_id`` — both monkeypatched in the
router module. ``sessions_client`` satisfies both member and admin auth.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

import services.sessions_service.routers.makeups as makeups_mod
from services.sessions_service.models import (
    MakeupBooking,
    MakeupOrigin,
    MakeupStatus,
    SessionBooking,
    SessionBookingStatus,
)
from tests.factories import SessionCoachFactory, SessionFactory

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

_DAY = datetime(2026, 6, 9, 6, 0, tzinfo=timezone.utc)


def _patch_member(monkeypatch, learner_id, auth_id="auth-learner"):
    async def _by_auth(a, *, calling_service):
        return {"id": str(learner_id), "auth_id": auth_id}

    async def _by_id(mid, *, calling_service):
        return {"id": mid, "auth_id": auth_id}

    monkeypatch.setattr(makeups_mod, "get_member_by_auth_id", _by_auth)
    monkeypatch.setattr(makeups_mod, "get_member_by_id", _by_id)


async def _make_session(db, coach_id, *, capacity=10):
    sess = SessionFactory.create(
        starts_at=_DAY,
        ends_at=_DAY + timedelta(hours=1),
        status="SCHEDULED",
        capacity=capacity,
    )
    db.add(sess)
    await db.flush()
    db.add(SessionCoachFactory.create(session_id=sess.id, coach_id=coach_id))
    await db.commit()
    return sess


async def test_learner_request_creates_requested(
    sessions_client, db_session, monkeypatch
):
    learner_id, coach_id = uuid.uuid4(), uuid.uuid4()
    _patch_member(monkeypatch, learner_id)
    sess = await _make_session(db_session, coach_id)

    r = await sessions_client.post(
        "/makeups/me/requests",
        json={
            "coach_member_id": str(coach_id),
            "scheduled_session_id": str(sess.id),
            "origin": "learner_reschedule",
            "reason": "Work travel",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "requested"
    assert body["learner_member_id"] == str(learner_id)


async def test_learner_request_reason_required(
    sessions_client, db_session, monkeypatch
):
    learner_id, coach_id = uuid.uuid4(), uuid.uuid4()
    _patch_member(monkeypatch, learner_id)
    sess = await _make_session(db_session, coach_id)

    r = await sessions_client.post(
        "/makeups/me/requests",
        json={
            "coach_member_id": str(coach_id),
            "scheduled_session_id": str(sess.id),
            "origin": "learner_reschedule",
        },
    )
    assert r.status_code == 422


async def test_learner_request_outstanding_cap(
    sessions_client, db_session, monkeypatch
):
    learner_id, coach_id = uuid.uuid4(), uuid.uuid4()
    _patch_member(monkeypatch, learner_id)
    sess = await _make_session(db_session, coach_id)
    db_session.add(
        MakeupBooking(
            learner_member_id=learner_id,
            coach_member_id=coach_id,
            origin=MakeupOrigin.EXCUSED_ABSENCE,
            status=MakeupStatus.REQUESTED,
        )
    )
    await db_session.commit()

    r = await sessions_client.post(
        "/makeups/me/requests",
        json={
            "coach_member_id": str(coach_id),
            "scheduled_session_id": str(sess.id),
            "origin": "excused_absence",
        },
    )
    assert r.status_code == 409


async def test_admin_confirm_request(sessions_client, db_session, monkeypatch):
    learner_id, coach_id = uuid.uuid4(), uuid.uuid4()
    _patch_member(monkeypatch, learner_id)
    sess = await _make_session(db_session, coach_id)
    mk = MakeupBooking(
        learner_member_id=learner_id,
        coach_member_id=coach_id,
        origin=MakeupOrigin.LEARNER_RESCHEDULE,
        status=MakeupStatus.REQUESTED,
        scheduled_session_id=sess.id,
    )
    db_session.add(mk)
    await db_session.commit()

    r = await sessions_client.post(f"/makeups/bookings/{mk.id}/confirm")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "confirmed"

    booking = (
        await db_session.execute(
            select(SessionBooking).where(
                SessionBooking.session_id == sess.id,
                SessionBooking.member_id == learner_id,
            )
        )
    ).scalar_one()
    assert booking.status == SessionBookingStatus.CONFIRMED


async def test_my_requests_lists(sessions_client, db_session, monkeypatch):
    learner_id, coach_id = uuid.uuid4(), uuid.uuid4()
    _patch_member(monkeypatch, learner_id)
    db_session.add(
        MakeupBooking(
            learner_member_id=learner_id,
            coach_member_id=coach_id,
            origin=MakeupOrigin.EXCUSED_ABSENCE,
            status=MakeupStatus.REQUESTED,
        )
    )
    await db_session.commit()

    r = await sessions_client.get("/makeups/me/requests")
    assert r.status_code == 200, r.text
    assert any(row["learner_member_id"] == str(learner_id) for row in r.json())
