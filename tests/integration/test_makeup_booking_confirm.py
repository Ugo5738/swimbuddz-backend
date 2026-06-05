"""Integration tests for the make-up booking confirm + list endpoints (Phase 1).

The confirm endpoint resolves the learner's auth_id via ``get_member_by_id``;
we monkeypatch that in the router module. ``sessions_client`` is admin-authed.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

import services.sessions_service.routers.makeups as makeups_mod
from services.sessions_service.models import (
    BookingChannel,
    MakeupBooking,
    MakeupOrigin,
    MakeupStatus,
    SessionBooking,
    SessionBookingStatus,
)
from tests.factories import SessionCoachFactory, SessionFactory

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

_DAY = datetime(2026, 6, 9, 6, 0, tzinfo=timezone.utc)


def _patch_member(monkeypatch, auth_id="auth-learner"):
    async def _fake(member_id, *, calling_service):
        return {"id": member_id, "auth_id": auth_id}

    monkeypatch.setattr(makeups_mod, "get_member_by_id", _fake)


async def _make_session(db, coach_id, *, start=_DAY, capacity=10, hours=1):
    sess = SessionFactory.create(
        starts_at=start,
        ends_at=start + timedelta(hours=hours),
        status="SCHEDULED",
        capacity=capacity,
    )
    db.add(sess)
    await db.flush()
    db.add(SessionCoachFactory.create(session_id=sess.id, coach_id=coach_id))
    await db.commit()
    return sess


def _payload(learner_id, coach_id, session_id, **extra):
    body = {
        "learner_member_id": str(learner_id),
        "coach_member_id": str(coach_id),
        "scheduled_session_id": str(session_id),
        "origin": "excused_absence",
    }
    body.update(extra)
    return body


async def test_confirm_makeup_happy_path(sessions_client, db_session, monkeypatch):
    _patch_member(monkeypatch)
    coach_id, learner_id = uuid.uuid4(), uuid.uuid4()
    sess = await _make_session(db_session, coach_id)

    r = await sessions_client.post(
        "/makeups/bookings", json=_payload(learner_id, coach_id, sess.id)
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "confirmed"
    assert body["scheduled_session_id"] == str(sess.id)
    assert body["learner_type"] == "cohort"

    booking = (
        await db_session.execute(
            select(SessionBooking).where(
                SessionBooking.session_id == sess.id,
                SessionBooking.member_id == learner_id,
            )
        )
    ).scalar_one()
    assert booking.status == SessionBookingStatus.CONFIRMED


async def test_reschedule_requires_reason(sessions_client, db_session, monkeypatch):
    _patch_member(monkeypatch)
    coach_id, learner_id = uuid.uuid4(), uuid.uuid4()
    sess = await _make_session(db_session, coach_id)

    r = await sessions_client.post(
        "/makeups/bookings",
        json=_payload(learner_id, coach_id, sess.id, origin="learner_reschedule"),
    )
    assert r.status_code == 422

    r2 = await sessions_client.post(
        "/makeups/bookings",
        json=_payload(
            learner_id, coach_id, sess.id, origin="learner_reschedule", reason="Travel"
        ),
    )
    assert r2.status_code == 201, r2.text


async def test_outstanding_cap(sessions_client, db_session, monkeypatch):
    _patch_member(monkeypatch)
    coach_id, learner_id = uuid.uuid4(), uuid.uuid4()
    sess = await _make_session(db_session, coach_id)
    db_session.add(
        MakeupBooking(
            learner_member_id=learner_id,
            coach_member_id=coach_id,
            origin=MakeupOrigin.EXCUSED_ABSENCE,
            status=MakeupStatus.CONFIRMED,
        )
    )
    await db_session.commit()

    r = await sessions_client.post(
        "/makeups/bookings", json=_payload(learner_id, coach_id, sess.id)
    )
    assert r.status_code == 409


async def test_session_full(sessions_client, db_session, monkeypatch):
    _patch_member(monkeypatch)
    coach_id, learner_id = uuid.uuid4(), uuid.uuid4()
    sess = await _make_session(db_session, coach_id, capacity=1)
    db_session.add(
        SessionBooking(
            session_id=sess.id,
            member_id=uuid.uuid4(),
            member_auth_id="x",
            status=SessionBookingStatus.CONFIRMED,
            channel=BookingChannel.ADMIN,
        )
    )
    await db_session.commit()

    r = await sessions_client.post(
        "/makeups/bookings", json=_payload(learner_id, coach_id, sess.id)
    )
    assert r.status_code == 409


async def test_window_exceeded(sessions_client, db_session, monkeypatch):
    _patch_member(monkeypatch)
    coach_id, learner_id = uuid.uuid4(), uuid.uuid4()
    sess = await _make_session(db_session, coach_id, start=_DAY)
    original = SessionFactory.create(
        starts_at=_DAY - timedelta(days=20),
        ends_at=_DAY - timedelta(days=20) + timedelta(hours=1),
        status="SCHEDULED",
    )
    db_session.add(original)
    await db_session.commit()

    r = await sessions_client.post(
        "/makeups/bookings",
        json=_payload(
            learner_id, coach_id, sess.id, original_session_id=str(original.id)
        ),
    )
    assert r.status_code == 422


async def test_coach_not_on_session(sessions_client, db_session, monkeypatch):
    _patch_member(monkeypatch)
    coach_id, learner_id = uuid.uuid4(), uuid.uuid4()
    sess = await _make_session(db_session, uuid.uuid4())  # led by another coach

    r = await sessions_client.post(
        "/makeups/bookings", json=_payload(learner_id, coach_id, sess.id)
    )
    assert r.status_code == 422


async def test_list_makeups_by_learner(sessions_client, db_session, monkeypatch):
    _patch_member(monkeypatch)
    coach_id, learner_id = uuid.uuid4(), uuid.uuid4()
    sess = await _make_session(db_session, coach_id)
    await sessions_client.post(
        "/makeups/bookings", json=_payload(learner_id, coach_id, sess.id)
    )

    r = await sessions_client.get(
        "/makeups/bookings", params={"learner_id": str(learner_id)}
    )
    assert r.status_code == 200, r.text
    rows = r.json()
    assert len(rows) >= 1
    assert all(row["learner_member_id"] == str(learner_id) for row in rows)


async def test_confirm_derives_cohort_block(sessions_client, db_session, monkeypatch):
    _patch_member(monkeypatch)
    coach_id, learner_id, cohort_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    target = await _make_session(db_session, coach_id)
    original = SessionFactory.create(
        starts_at=_DAY - timedelta(days=2),
        ends_at=_DAY - timedelta(days=2) + timedelta(hours=1),
        status="SCHEDULED",
        cohort_id=cohort_id,  # → COHORT_CLASS
    )
    db_session.add(original)
    await db_session.commit()

    r = await sessions_client.post(
        "/makeups/bookings",
        json=_payload(
            learner_id, coach_id, target.id, original_session_id=str(original.id)
        ),
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["block_kind"] == "cohort_term"
    assert body["block_id"] == str(cohort_id)


async def test_obligation_flip_invoked(sessions_client, db_session, monkeypatch):
    _patch_member(monkeypatch)
    calls = []

    async def _fake_flip(
        obligation_id, scheduled_session_id, *, calling_service, notes=None
    ):
        calls.append((obligation_id, scheduled_session_id))
        return {"id": obligation_id, "status": "scheduled"}

    monkeypatch.setattr(makeups_mod, "schedule_makeup_obligation", _fake_flip)
    coach_id, learner_id, obligation_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    sess = await _make_session(db_session, coach_id)

    r = await sessions_client.post(
        "/makeups/bookings",
        json=_payload(learner_id, coach_id, sess.id, obligation_id=str(obligation_id)),
    )
    assert r.status_code == 201, r.text
    assert calls == [(str(obligation_id), str(sess.id))]


async def test_obligation_flip_failure_does_not_block(
    sessions_client, db_session, monkeypatch
):
    _patch_member(monkeypatch)

    async def _boom(*a, **k):
        raise RuntimeError("payments down")

    monkeypatch.setattr(makeups_mod, "schedule_makeup_obligation", _boom)
    coach_id, learner_id, obligation_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    sess = await _make_session(db_session, coach_id)

    r = await sessions_client.post(
        "/makeups/bookings",
        json=_payload(learner_id, coach_id, sess.id, obligation_id=str(obligation_id)),
    )
    # flip failure is logged, the booking still confirms
    assert r.status_code == 201, r.text
