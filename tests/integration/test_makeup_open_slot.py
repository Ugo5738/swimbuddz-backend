"""Integration tests for the open-slot make-up endpoint (Phase 2).

``POST /makeups/open-slot`` creates a dedicated COHORT_CLASS make-up session in
a coach's open availability slot and confirms the learner into it in one step.
The learner's auth_id is resolved via ``get_member_by_id`` (monkeypatched).
``sessions_client`` is admin-authed.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

import services.sessions_service.routers.makeups as makeups_mod
from services.sessions_service.models import (
    MakeupBooking,
    MakeupOrigin,
    MakeupStatus,
    Session,
    SessionBooking,
    SessionBookingStatus,
    SessionCoach,
    SessionStatus,
    SessionType,
)
from tests.factories import SessionCoachFactory, SessionFactory

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

# Comfortably in the future so the endpoint's "slot must be in the future" guard
# passes regardless of when the suite runs.
_NOW = datetime.now(timezone.utc)
_SLOT_START = _NOW + timedelta(days=2)
_SLOT_END = _SLOT_START + timedelta(hours=1)


def _patch_member(monkeypatch, auth_id="auth-learner"):
    async def _fake(member_id, *, calling_service):
        return {"id": member_id, "auth_id": auth_id}

    monkeypatch.setattr(makeups_mod, "get_member_by_id", _fake)


def _payload(learner_id, coach_id, *, starts_at=_SLOT_START, ends_at=_SLOT_END, **extra):
    body = {
        "learner_member_id": str(learner_id),
        "coach_member_id": str(coach_id),
        "starts_at": starts_at.isoformat(),
        "ends_at": ends_at.isoformat(),
        "origin": "excused_absence",
    }
    body.update(extra)
    return body


async def test_open_slot_happy_path(sessions_client, db_session, monkeypatch):
    """Creates a dedicated session, attaches the coach, books the learner in."""
    _patch_member(monkeypatch)
    coach_id, learner_id, cohort_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    r = await sessions_client.post(
        "/makeups/open-slot",
        json=_payload(learner_id, coach_id, cohort_id=str(cohort_id)),
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "confirmed"
    new_session_id = uuid.UUID(body["scheduled_session_id"])

    # A new COHORT_CLASS session was created with the right cohort + default cap 1.
    sess = (
        await db_session.execute(select(Session).where(Session.id == new_session_id))
    ).scalar_one()
    assert sess.session_type == SessionType.COHORT_CLASS
    assert sess.cohort_id == cohort_id
    assert sess.capacity == 1
    assert sess.status == SessionStatus.SCHEDULED

    # Coach attached.
    coach_row = (
        await db_session.execute(
            select(SessionCoach).where(SessionCoach.session_id == new_session_id)
        )
    ).scalar_one()
    assert coach_row.coach_id == coach_id

    # Learner booked in (CONFIRMED).
    booking = (
        await db_session.execute(
            select(SessionBooking).where(
                SessionBooking.session_id == new_session_id,
                SessionBooking.member_id == learner_id,
            )
        )
    ).scalar_one()
    assert booking.status == SessionBookingStatus.CONFIRMED


async def test_open_slot_derives_cohort_from_original(
    sessions_client, db_session, monkeypatch
):
    """With no explicit cohort_id, the cohort + block come from the missed session."""
    _patch_member(monkeypatch)
    coach_id, learner_id, cohort_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    original = SessionFactory.create(
        starts_at=_NOW - timedelta(days=1),
        ends_at=_NOW - timedelta(days=1) + timedelta(hours=1),
        status="SCHEDULED",
        cohort_id=cohort_id,
    )
    db_session.add(original)
    await db_session.commit()

    r = await sessions_client.post(
        "/makeups/open-slot",
        json=_payload(learner_id, coach_id, original_session_id=str(original.id)),
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["block_kind"] == "cohort_term"
    assert body["block_id"] == str(cohort_id)

    new_session_id = uuid.UUID(body["scheduled_session_id"])
    sess = (
        await db_session.execute(select(Session).where(Session.id == new_session_id))
    ).scalar_one()
    assert sess.cohort_id == cohort_id


async def test_open_slot_future_required(sessions_client, db_session, monkeypatch):
    """A slot in the past is rejected."""
    _patch_member(monkeypatch)
    coach_id, learner_id, cohort_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    past = _NOW - timedelta(days=1)

    r = await sessions_client.post(
        "/makeups/open-slot",
        json=_payload(
            learner_id,
            coach_id,
            starts_at=past,
            ends_at=past + timedelta(hours=1),
            cohort_id=str(cohort_id),
        ),
    )
    assert r.status_code == 422


async def test_open_slot_requires_cohort_or_original(
    sessions_client, db_session, monkeypatch
):
    """Neither cohort_id nor original_session_id → schema rejects (422)."""
    _patch_member(monkeypatch)
    coach_id, learner_id = uuid.uuid4(), uuid.uuid4()

    r = await sessions_client.post(
        "/makeups/open-slot", json=_payload(learner_id, coach_id)
    )
    assert r.status_code == 422


async def test_open_slot_reschedule_requires_reason(
    sessions_client, db_session, monkeypatch
):
    """A learner_reschedule needs a reason even via the open-slot path (1b)."""
    _patch_member(monkeypatch)
    coach_id, learner_id, cohort_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    r = await sessions_client.post(
        "/makeups/open-slot",
        json=_payload(
            learner_id, coach_id, cohort_id=str(cohort_id), origin="learner_reschedule"
        ),
    )
    assert r.status_code == 422

    r2 = await sessions_client.post(
        "/makeups/open-slot",
        json=_payload(
            learner_id,
            coach_id,
            cohort_id=str(cohort_id),
            origin="learner_reschedule",
            reason="Travel",
        ),
    )
    assert r2.status_code == 201, r2.text


async def test_open_slot_coach_overlap_rejected(
    sessions_client, db_session, monkeypatch
):
    """A slot overlapping a session the coach already runs is rejected (use join)."""
    _patch_member(monkeypatch)
    coach_id, learner_id, cohort_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    # Existing session led by the coach, overlapping the requested slot.
    existing = SessionFactory.create(
        starts_at=_SLOT_START - timedelta(minutes=30),
        ends_at=_SLOT_START + timedelta(minutes=30),
        status="SCHEDULED",
        capacity=10,
    )
    db_session.add(existing)
    await db_session.flush()
    db_session.add(SessionCoachFactory.create(session_id=existing.id, coach_id=coach_id))
    await db_session.commit()

    r = await sessions_client.post(
        "/makeups/open-slot",
        json=_payload(learner_id, coach_id, cohort_id=str(cohort_id)),
    )
    assert r.status_code == 409


async def test_open_slot_outstanding_rejected(
    sessions_client, db_session, monkeypatch
):
    """An ineligible learner (outstanding make-up) is rejected *before* any session
    is built — fail-fast guard, so nothing is left behind."""
    _patch_member(monkeypatch)
    coach_id, learner_id, cohort_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
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
        "/makeups/open-slot",
        json=_payload(learner_id, coach_id, cohort_id=str(cohort_id)),
    )
    assert r.status_code == 409

    # Rejected before the session was created — none led by this coach.
    led = (
        await db_session.execute(
            select(func.count())
            .select_from(SessionCoach)
            .where(SessionCoach.coach_id == coach_id)
        )
    ).scalar_one()
    assert led == 0
