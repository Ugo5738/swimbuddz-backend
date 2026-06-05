"""Integration tests for the coach availability editor endpoints.

GET/PUT ``/coaches/me/availability`` — Phase 0 of make-up scheduling.
See docs/design/AVAILABILITY_AND_MAKEUP_SCHEDULING_DESIGN.md.
"""

import pytest

from services.members_service.app.main import app as members_app
from tests.conftest import make_coach_user, make_member_user, override_auth
from tests.factories import CoachProfileFactory, MemberFactory

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def _create_coach(db_session, auth_id: str):
    """Insert a Member + CoachProfile sharing ``auth_id``; return (member, coach)."""
    member = MemberFactory.create(auth_id=auth_id)
    db_session.add(member)
    await db_session.flush()
    coach = CoachProfileFactory.create(member_id=member.id)
    db_session.add(coach)
    await db_session.commit()
    return member, coach


_VALID_PAYLOAD = {
    "availability": {
        "timezone": "Africa/Lagos",
        "recurring": [
            {"weekday": "tue", "start": "06:00", "end": "10:00"},
            {"weekday": "sat", "start": "08:00", "end": "12:00"},
        ],
        "blackouts": [{"start": "2026-06-15", "end": "2026-06-22", "reason": "travel"}],
        "slot_minutes": 60,
    },
    "min_hours_between_sessions": 72,
}


async def test_put_then_get_availability(members_client, db_session):
    """A coach can publish availability and read it back, fully typed."""
    auth_id = "coach-avail-happy"
    await _create_coach(db_session, auth_id)

    with override_auth(members_app, make_coach_user(user_id=auth_id)):
        put = await members_client.put("/coaches/me/availability", json=_VALID_PAYLOAD)
        assert put.status_code == 200, put.text
        body = put.json()
        assert body["min_hours_between_sessions"] == 72
        assert len(body["availability"]["recurring"]) == 2
        assert body["availability"]["slot_minutes"] == 60

        got = await members_client.get("/coaches/me/availability")
        assert got.status_code == 200
        data = got.json()
        assert data["availability"]["timezone"] == "Africa/Lagos"
        assert data["availability"]["recurring"][0]["weekday"] == "tue"
        assert data["availability"]["blackouts"][0]["reason"] == "travel"
        assert data["min_hours_between_sessions"] == 72


async def test_get_unset_returns_null(members_client, db_session):
    """A coach who hasn't set availability gets nulls, not an error."""
    auth_id = "coach-avail-unset"
    await _create_coach(db_session, auth_id)

    with override_auth(members_app, make_coach_user(user_id=auth_id)):
        got = await members_client.get("/coaches/me/availability")
        assert got.status_code == 200
        data = got.json()
        assert data["availability"] is None
        assert data["min_hours_between_sessions"] is None


async def test_put_rejects_same_day_overlap(members_client, db_session):
    """Overlapping blocks on the same weekday are rejected (422)."""
    auth_id = "coach-avail-overlap"
    await _create_coach(db_session, auth_id)
    payload = {
        "availability": {
            "recurring": [
                {"weekday": "mon", "start": "06:00", "end": "10:00"},
                {"weekday": "mon", "start": "09:00", "end": "11:00"},
            ]
        }
    }
    with override_auth(members_app, make_coach_user(user_id=auth_id)):
        resp = await members_client.put("/coaches/me/availability", json=payload)
        assert resp.status_code == 422


async def test_put_rejects_bad_time_format(members_client, db_session):
    """Non ``HH:MM`` times are rejected (422)."""
    auth_id = "coach-avail-badtime"
    await _create_coach(db_session, auth_id)
    payload = {
        "availability": {
            "recurring": [{"weekday": "mon", "start": "6am", "end": "10:00"}]
        }
    }
    with override_auth(members_app, make_coach_user(user_id=auth_id)):
        resp = await members_client.put("/coaches/me/availability", json=payload)
        assert resp.status_code == 422


async def test_put_rejects_min_hours_out_of_bounds(members_client, db_session):
    """``min_hours_between_sessions`` above the cap is rejected (422)."""
    auth_id = "coach-avail-minhours"
    await _create_coach(db_session, auth_id)
    payload = {"availability": {"recurring": []}, "min_hours_between_sessions": 999}
    with override_auth(members_app, make_coach_user(user_id=auth_id)):
        resp = await members_client.put("/coaches/me/availability", json=payload)
        assert resp.status_code == 422


async def test_non_coach_member_gets_404(members_client, db_session):
    """A member without a CoachProfile cannot read coach availability."""
    auth_id = "not-a-coach"
    member = MemberFactory.create(auth_id=auth_id)
    db_session.add(member)
    await db_session.commit()
    with override_auth(members_app, make_member_user(user_id=auth_id)):
        resp = await members_client.get("/coaches/me/availability")
        assert resp.status_code == 404
