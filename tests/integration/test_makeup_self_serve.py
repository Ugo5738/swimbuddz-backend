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
    BookingChannel,
    MakeupBlockKind,
    MakeupBooking,
    MakeupOrigin,
    MakeupStatus,
    Session,
    SessionBooking,
    SessionBookingStatus,
    SessionCoach,
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


# ---------------------------------------------------------------------------
# Open-slot self-serve requests (design §4 Phase 2)
# ---------------------------------------------------------------------------


async def _enroll_learner_with_coach(db, coach_id, learner_id, cohort_id):
    """A session in ``cohort_id`` led by ``coach_id`` that ``learner_id`` booked —
    enough for the open-slot cohort auto-derivation."""
    sess = SessionFactory.create(
        starts_at=_DAY,
        ends_at=_DAY + timedelta(hours=1),
        status="SCHEDULED",
        cohort_id=cohort_id,
    )
    db.add(sess)
    await db.flush()
    db.add(SessionCoachFactory.create(session_id=sess.id, coach_id=coach_id))
    db.add(
        SessionBooking(
            session_id=sess.id,
            member_id=learner_id,
            member_auth_id="x",
            status=SessionBookingStatus.CONFIRMED,
            channel=BookingChannel.ADMIN,
        )
    )
    await db.commit()
    return sess


def _future(hours: int = 72):
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).replace(microsecond=0)


async def test_learner_request_open_slot(sessions_client, db_session, monkeypatch):
    """A learner requests a coach's open time → REQUESTED with cohort auto-derived."""
    learner_id, coach_id, cohort_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    _patch_member(monkeypatch, learner_id)
    await _enroll_learner_with_coach(db_session, coach_id, learner_id, cohort_id)
    start = _future()

    r = await sessions_client.post(
        "/makeups/me/requests",
        json={
            "coach_member_id": str(coach_id),
            "starts_at": start.isoformat(),
            "ends_at": (start + timedelta(hours=1)).isoformat(),
            "origin": "learner_reschedule",
            "reason": "Work travel",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "requested"
    assert body["scheduled_session_id"] is None
    assert body["requested_start_at"] is not None
    assert body["block_id"] == str(cohort_id)


async def test_learner_request_open_slot_no_cohort(
    sessions_client, db_session, monkeypatch
):
    """No shared cohort with the coach → can't auto-derive → 422."""
    learner_id, coach_id = uuid.uuid4(), uuid.uuid4()
    _patch_member(monkeypatch, learner_id)
    start = _future()

    r = await sessions_client.post(
        "/makeups/me/requests",
        json={
            "coach_member_id": str(coach_id),
            "starts_at": start.isoformat(),
            "ends_at": (start + timedelta(hours=1)).isoformat(),
            "origin": "learner_reschedule",
            "reason": "Work travel",
        },
    )
    assert r.status_code == 422


async def test_confirm_open_slot_request_creates_session(
    sessions_client, db_session, monkeypatch
):
    """Admin confirm of an open-slot request creates the session + books in."""
    learner_id, coach_id, cohort_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    _patch_member(monkeypatch, learner_id)
    start = _future()
    mk = MakeupBooking(
        learner_member_id=learner_id,
        coach_member_id=coach_id,
        origin=MakeupOrigin.LEARNER_RESCHEDULE,
        status=MakeupStatus.REQUESTED,
        block_kind=MakeupBlockKind.COHORT_TERM,
        block_id=cohort_id,
        requested_start_at=start,
        requested_end_at=start + timedelta(hours=1),
    )
    db_session.add(mk)
    await db_session.commit()

    r = await sessions_client.post(f"/makeups/bookings/{mk.id}/confirm")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "confirmed"
    new_session_id = uuid.UUID(body["scheduled_session_id"])

    sess = (
        await db_session.execute(select(Session).where(Session.id == new_session_id))
    ).scalar_one()
    assert sess.cohort_id == cohort_id
    coach_row = (
        await db_session.execute(
            select(SessionCoach).where(SessionCoach.session_id == new_session_id)
        )
    ).scalar_one()
    assert coach_row.coach_id == coach_id
    booking = (
        await db_session.execute(
            select(SessionBooking).where(
                SessionBooking.session_id == new_session_id,
                SessionBooking.member_id == learner_id,
            )
        )
    ).scalar_one()
    assert booking.status == SessionBookingStatus.CONFIRMED
