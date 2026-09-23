"""End-to-end boundaries for Event-backed Community Swim Sessions."""

import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from libs.common.datetime_utils import utc_now
from services.events_service.models import Event
from services.events_service.routers.member import get_current_member
from services.sessions_service.models import Session, SessionStatus
from tests.factories import SessionFactory


def _event(*, created_by: uuid.UUID, tier_access: str = "public") -> Event:
    starts_at = utc_now() + timedelta(days=14)
    return Event(
        title=f"Community Swim {uuid.uuid4().hex[:6]}",
        description="A swim open across SwimBuddz programmes.",
        event_type="community_swim",
        audience="community",
        primary_audience="community",
        audiences=["community", "club", "academy"],
        visibility="public",
        status="published",
        location_type="physical",
        timezone="Africa/Lagos",
        location="Rowe Park Pool",
        start_time=starts_at,
        end_time=starts_at + timedelta(hours=2),
        max_capacity=30,
        tier_access=tier_access,
        created_by=created_by,
    )


@pytest.mark.asyncio
@pytest.mark.integration
async def test_event_attendance_policy_allows_every_member_product_and_scopes_restricted_tiers(
    events_client, db_session
):
    creator = uuid.uuid4()
    public_event = _event(created_by=creator, tier_access="public")
    club_event = _event(created_by=creator, tier_access="club")
    academy_event = _event(created_by=creator, tier_access="academy")
    db_session.add_all([public_event, club_event, academy_event])
    await db_session.commit()

    for paid_tier in ("community", "club", "academy"):
        public_check = await events_client.post(
            "/internal/events/attendance/checks",
            json={
                "event_ids": [str(public_event.id)],
                "member_id": str(uuid.uuid4()),
                "paid_tiers": [paid_tier],
            },
        )
        assert public_check.status_code == 200
        assert public_check.json()[str(public_event.id)]["allowed"] is True

    restricted = await events_client.post(
        "/internal/events/attendance/checks",
        json={
            "event_ids": [str(club_event.id), str(academy_event.id)],
            "member_id": str(uuid.uuid4()),
            "paid_tiers": ["academy"],
        },
    )
    assert restricted.status_code == 200
    assert restricted.json()[str(club_event.id)]["allowed"] is False
    assert restricted.json()[str(academy_event.id)]["allowed"] is True


@pytest.mark.asyncio
@pytest.mark.integration
async def test_community_swim_can_create_only_one_active_session_from_event_contract(
    sessions_client, db_session
):
    event_id = uuid.uuid4()
    starts_at = utc_now() + timedelta(days=10)
    contract = {
        "event_id": str(event_id),
        "event_type": "community_swim",
        "status": "published",
        "tier_access": "public",
        "title": "Monthly Community Swim",
        "description": "Everyone swims together.",
        "starts_at": starts_at.isoformat(),
        "ends_at": (starts_at + timedelta(hours=2)).isoformat(),
        "timezone": "Africa/Lagos",
        "pool_id": None,
        "location_name": "Rowe Park Pool",
        "capacity": 30,
        "expected_session_count": 1,
    }
    payload = {
        "title": "Client value is not authoritative",
        "description": "Client value is not authoritative",
        "session_type": "event",
        "event_id": str(event_id),
        "starts_at": (starts_at + timedelta(days=1)).isoformat(),
        "ends_at": (starts_at + timedelta(days=1, hours=1)).isoformat(),
        "capacity": 5,
        "pool_fee": 5200,
        "allows_guests": True,
    }

    with patch(
        "services.sessions_service.routers.member.get_event_session_contract",
        new=AsyncMock(return_value=contract),
    ):
        created = await sessions_client.post("/sessions/", json=payload)
        duplicate = await sessions_client.post("/sessions/", json=payload)

    assert created.status_code == 201, created.text
    assert created.json()["title"] == contract["title"]
    assert (
        datetime.fromisoformat(created.json()["starts_at"].replace("Z", "+00:00"))
        == starts_at
    )
    assert created.json()["capacity"] == 30
    assert created.json()["allows_guests"] is True
    assert duplicate.status_code == 409


@pytest.mark.asyncio
@pytest.mark.integration
async def test_event_shared_fields_sync_and_cancellation_preserve_session_history(
    sessions_client, db_session
):
    event_id = uuid.uuid4()
    session = SessionFactory.create(event_id=event_id)
    db_session.add(session)
    await db_session.commit()
    next_start = session.starts_at + timedelta(days=7)

    synced = await sessions_client.patch(
        f"/internal/sessions/events/{event_id}/sync",
        json={
            "title": "Updated Community Swim",
            "starts_at": next_start.isoformat(),
            "ends_at": (next_start + timedelta(hours=3)).isoformat(),
            "location_name": "New Pool",
            "capacity": 40,
        },
    )
    assert synced.status_code == 200, synced.text
    await db_session.refresh(session)
    assert session.title == "Updated Community Swim"
    assert session.location_name == "New Pool"
    assert session.capacity == 40

    with (
        patch(
            "services.sessions_service.routers.internal.internal_post",
            new=AsyncMock(),
        ),
        patch(
            "services.sessions_service.routers.internal.cancel_opportunities_for_context",
            new=AsyncMock(),
        ),
    ):
        cancelled = await sessions_client.patch(
            f"/internal/sessions/events/{event_id}/sync",
            json={"cancel": True, "cancellation_reason": "Event cancelled"},
        )
    assert cancelled.status_code == 200
    persisted = await db_session.scalar(select(Session).where(Session.id == session.id))
    assert persisted is not None
    assert persisted.status == SessionStatus.CANCELLED


@pytest.mark.asyncio
@pytest.mark.integration
async def test_linked_event_rejects_rsvp_and_delete_soft_cancels_parent(
    events_client, db_session
):
    from services.events_service.app.main import app as events_app

    member = SimpleNamespace(id=uuid.uuid4(), auth_id="linked-event-member")
    event = _event(created_by=member.id)
    db_session.add(event)
    await db_session.commit()

    async def _member_override():
        return member

    events_app.dependency_overrides[get_current_member] = _member_override
    links = AsyncMock(return_value={"linked_count": 1, "active_count": 1})
    sync = AsyncMock(
        return_value={
            "event_id": str(event.id),
            "linked_count": 1,
            "updated_count": 0,
            "cancelled_count": 1,
        }
    )
    try:
        with (
            patch(
                "services.events_service.routers.member.get_event_session_links",
                links,
            ),
            patch(
                "services.events_service.routers.member.sync_event_sessions",
                sync,
            ),
        ):
            rsvp = await events_client.post(
                f"/events/{event.id}/rsvp", json={"status": "going"}
            )
            deleted = await events_client.delete(f"/events/{event.id}")
    finally:
        events_app.dependency_overrides.pop(get_current_member, None)

    assert rsvp.status_code == 409
    assert "linked Session" in rsvp.json()["detail"]
    assert deleted.status_code == 204
    sync.assert_awaited_once()
    await db_session.refresh(event)
    assert event.status == "cancelled"
