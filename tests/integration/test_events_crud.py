"""Integration tests for events_service — event CRUD + RSVP guards.

The thing most worth pinning here is the naira↔kobo boundary: the API
speaks naira (float), the DB stores kobo (int). A wrong factor on
create *or* update *or* the read-back conversion silently mis-prices
every event. Plus the lifecycle guards: 404s are clean, deleting an
event cascades its RSVPs, and re-RSVPing updates the row instead of
duplicating it.

`get_current_member` resolves via the local `members` table
(MemberRef.auth_id == JWT.user_id) — no cross-service HTTP — so we seed
a real Member with a known auth_id and point the app's auth deps at it
(the test_members_pods / transport pattern). Chat-channel sync is
patched out; chat downtime must never block an event write.

Not in scope (follow-up): pay_with_bubbles wallet debit + the 402/403
mapping (wallet suite), capacity enforcement, tier-gated visibility.
"""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from libs.auth.dependencies import (
    get_current_user,
    require_admin,
    require_service_role,
)

from tests.conftest import make_admin_user
from tests.factories import MemberFactory

_ENSURE = "services.events_service.routers.member.ensure_event_channel"
_RECONCILE = "services.events_service.routers.member.reconcile_event_membership"


def _silence_chat_sync():
    return (
        patch(_ENSURE, new_callable=AsyncMock, return_value=None),
        patch(_RECONCILE, new_callable=AsyncMock, return_value=None),
    )


async def _setup_member(db_session):
    """Seed a Member with a known auth_id and point the events app's auth
    deps at it. Returns the seeded Member."""
    unique = uuid.uuid4().hex[:8]
    user = make_admin_user(
        user_id=str(uuid.uuid4()), email=f"organiser-{unique}@test.com"
    )
    member = MemberFactory.create(auth_id=user.user_id, email=user.email)
    db_session.add(member)
    await db_session.commit()

    from services.events_service.app.main import app

    async def _get_user():
        return user

    app.dependency_overrides[get_current_user] = _get_user
    app.dependency_overrides[require_admin] = _get_user
    app.dependency_overrides[require_service_role] = _get_user
    return member


def _event_payload(**overrides):
    s = uuid.uuid4().hex[:6]
    start = datetime.now(timezone.utc) + timedelta(days=7)
    d = {
        "title": f"Beach Day {s}",
        "event_type": "social",
        "location": "Tarkwa Bay",
        "start_time": start.isoformat(),
        "tier_access": "community",
        "cost_naira": 2500.0,
    }
    d.update(overrides)
    return d


# ---------------------------------------------------------------------------
# create — the naira→kobo boundary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_create_event_converts_naira_and_stamps_creator(
    events_client, db_session
):
    from sqlalchemy import select

    from services.events_service.models import Event

    member = await _setup_member(db_session)
    e, r = _silence_chat_sync()
    with e, r:
        resp = await events_client.post("/events/", json=_event_payload())
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["cost_naira"] == 2500.0
    assert body["created_by"] == str(member.id)

    # DB stores kobo, not naira.
    row = (
        await db_session.execute(select(Event).where(Event.id == uuid.UUID(body["id"])))
    ).scalar_one()
    assert row.cost_kobo == 250000


@pytest.mark.asyncio
@pytest.mark.integration
async def test_create_free_event_keeps_cost_null(events_client, db_session):
    await _setup_member(db_session)
    e, r = _silence_chat_sync()
    with e, r:
        resp = await events_client.post(
            "/events/", json=_event_payload(cost_naira=None)
        )
    assert resp.status_code == 201, resp.text
    assert resp.json()["cost_naira"] is None


# ---------------------------------------------------------------------------
# get / update / delete lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_unknown_event_404(events_client):
    resp = await events_client.get(f"/events/{uuid.uuid4()}")
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
@pytest.mark.integration
async def test_update_unknown_event_404(events_client):
    resp = await events_client.patch(f"/events/{uuid.uuid4()}", json={"title": "x"})
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
@pytest.mark.integration
async def test_patch_event_reprices_in_kobo(events_client, db_session):
    """A cost_naira PATCH must round-trip through kobo on read-back."""
    from sqlalchemy import select

    from services.events_service.models import Event

    await _setup_member(db_session)
    e, r = _silence_chat_sync()
    with e, r:
        created = await events_client.post("/events/", json=_event_payload())
    event_id = created.json()["id"]

    upd = await events_client.patch(f"/events/{event_id}", json={"cost_naira": 999.5})
    assert upd.status_code == 200, upd.text
    assert upd.json()["cost_naira"] == 999.5

    row = (
        await db_session.execute(select(Event).where(Event.id == uuid.UUID(event_id)))
    ).scalar_one()
    assert row.cost_kobo == 99950


@pytest.mark.asyncio
@pytest.mark.integration
async def test_delete_unknown_event_404(events_client):
    resp = await events_client.delete(f"/events/{uuid.uuid4()}")
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
@pytest.mark.integration
async def test_delete_event_cascades_rsvps(events_client, db_session):
    from sqlalchemy import func, select

    from services.events_service.models import EventRSVP

    await _setup_member(db_session)
    e, r = _silence_chat_sync()
    with e, r:
        created = await events_client.post("/events/", json=_event_payload())
        event_id = created.json()["id"]
        rsvp = await events_client.post(
            f"/events/{event_id}/rsvp", json={"status": "going"}
        )
        assert rsvp.status_code == 200, rsvp.text

    deleted = await events_client.delete(f"/events/{event_id}")
    assert deleted.status_code == 204, deleted.text

    remaining = (
        await db_session.execute(
            select(func.count())
            .select_from(EventRSVP)
            .where(EventRSVP.event_id == uuid.UUID(event_id))
        )
    ).scalar_one()
    assert remaining == 0


# ---------------------------------------------------------------------------
# RSVP guards
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_rsvp_unknown_event_404(events_client, db_session):
    await _setup_member(db_session)
    e, r = _silence_chat_sync()
    with e, r:
        resp = await events_client.post(
            f"/events/{uuid.uuid4()}/rsvp", json={"status": "going"}
        )
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
@pytest.mark.integration
async def test_rsvp_is_idempotent_per_member(events_client, db_session):
    """Re-RSVPing flips the existing row's status — never a duplicate."""
    from sqlalchemy import func, select

    from services.events_service.models import EventRSVP

    await _setup_member(db_session)
    e, r = _silence_chat_sync()
    with e, r:
        created = await events_client.post("/events/", json=_event_payload())
        event_id = created.json()["id"]

        first = await events_client.post(
            f"/events/{event_id}/rsvp", json={"status": "going"}
        )
        assert first.status_code == 200, first.text
        assert first.json()["status"] == "going"

        second = await events_client.post(
            f"/events/{event_id}/rsvp", json={"status": "not_going"}
        )
    assert second.status_code == 200, second.text
    assert second.json()["status"] == "not_going"
    assert second.json()["id"] == first.json()["id"]

    count = (
        await db_session.execute(
            select(func.count())
            .select_from(EventRSVP)
            .where(EventRSVP.event_id == uuid.UUID(event_id))
        )
    ).scalar_one()
    assert count == 1
