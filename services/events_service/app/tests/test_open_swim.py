"""Tests for member-created open-swim meets (events_service).

Covers the create gates (adults-only, per-swimmer pools, quota, fee snapshot),
the RSVP charge + waiver path (incl. the maybe→going charge fix), and
cancel-with-refund. Cross-service calls are patched where they are imported
into the router module; the `events` table is exercised against the dev DB via
the shared transactional `db_session` fixture.
"""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from libs.db.session import get_async_db
from services.events_service.app.main import app
from services.events_service.models import Event
from services.events_service.routers.member import get_current_member

MOCK_MEMBER_ID = uuid.uuid4()
MOCK_AUTH_ID = "test-auth-open-swim"

_MEMBER = "services.events_service.routers.member"


def _future(days: int = 2) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


async def _mock_current_member():
    from services.events_service.models import MemberRef

    return MemberRef(id=MOCK_MEMBER_ID, auth_id=MOCK_AUTH_ID)


@pytest_asyncio.fixture
async def client(db_session: AsyncSession):
    async def _get_db():
        yield db_session

    app.dependency_overrides[get_async_db] = _get_db
    app.dependency_overrides[get_current_member] = _mock_current_member
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def mocks():
    """Patch the cross-service calls the router makes. Defaults: adult member,
    a per-swimmer active-partner pool, successful wallet ops, no-op chat sync."""
    adult_dob = (datetime.now(timezone.utc) - timedelta(days=365 * 30)).isoformat()
    with (
        patch(
            f"{_MEMBER}.get_member_by_id",
            new=AsyncMock(
                return_value={
                    "id": str(MOCK_MEMBER_ID),
                    "auth_id": MOCK_AUTH_ID,
                    "date_of_birth": adult_dob,
                }
            ),
        ) as m_member,
        patch(
            f"{_MEMBER}.get_partner_pool",
            new=AsyncMock(
                return_value={
                    "id": str(uuid.uuid4()),
                    "name": "Test Pool",
                    "price_per_swimmer_ngn": "500",
                    "flat_session_fee_ngn": None,
                    "max_swimmers_capacity": 20,
                }
            ),
        ) as m_pool,
        patch(
            f"{_MEMBER}.debit_member_wallet",
            new=AsyncMock(return_value={"transaction_id": str(uuid.uuid4())}),
        ) as m_debit,
        patch(
            f"{_MEMBER}.credit_member_wallet", new=AsyncMock(return_value={})
        ) as m_credit,
        patch(
            f"{_MEMBER}.get_members_bulk",
            new=AsyncMock(
                return_value=[{"id": str(MOCK_MEMBER_ID), "auth_id": MOCK_AUTH_ID}]
            ),
        ) as m_bulk,
        patch(f"{_MEMBER}.ensure_event_channel", new=AsyncMock()) as m_chan,
        patch(f"{_MEMBER}.reconcile_event_membership", new=AsyncMock()) as m_recon,
    ):
        yield {
            "member": m_member,
            "pool": m_pool,
            "debit": m_debit,
            "credit": m_credit,
            "bulk": m_bulk,
            "chan": m_chan,
            "recon": m_recon,
        }


# ---------------------------------------------------------------------------
# Legacy creation is disabled: approval and venue proof are mandatory.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_legacy_open_swim_post_cannot_publish(client, mocks):
    response = await client.post(
        "/events/open-swim",
        json={
            "title": "Paid meet",
            "start_time": _future(),
            "pool_id": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 409
    mocks["pool"].assert_not_called()


@pytest.mark.asyncio
async def test_swim_plan_submission_does_not_publish_or_charge(
    client, mocks, db_session
):
    pool_id = str(uuid.uuid4())
    with patch(
        "services.events_service.routers.swim_plans.get_partner_pool",
        new=AsyncMock(return_value={"id": pool_id, "name": "Test Pool"}),
    ):
        response = await client.post(
            "/events/swim-plans",
            json={
                "kind": "open",
                "title": "Saturday afternoon laps",
                "pool_id": pool_id,
                "starts_at": _future(),
                "ends_at": (
                    datetime.now(timezone.utc) + timedelta(days=2, hours=1)
                ).isoformat(),
                "participants": 8,
            },
        )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["approval_status"] == "approved"
    assert body["venue_status"] == "pending"
    assert body["status"] == "pending_availability"
    assert body["linked_event_id"] is None
    mocks["debit"].assert_not_called()
    from sqlalchemy import select

    assert (
        await db_session.execute(select(Event).where(Event.event_type == "open_swim"))
    ).scalars().all() == []


@pytest.mark.asyncio
async def test_swim_plan_rejects_minor(client, mocks):
    mocks["member"].return_value["date_of_birth"] = (
        datetime.now(timezone.utc) - timedelta(days=365 * 12)
    ).isoformat()
    response = await client.post(
        "/events/swim-plans",
        json={
            "kind": "solo",
            "title": "Just me",
            "location": "Yaba",
            "starts_at": _future(),
            "ends_at": (
                datetime.now(timezone.utc) + timedelta(days=2, hours=1)
            ).isoformat(),
        },
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_public_plan_requires_partner_pool(client, mocks):
    response = await client.post(
        "/events/swim-plans",
        json={
            "kind": "open",
            "title": "Come swim",
            "starts_at": _future(),
            "ends_at": (
                datetime.now(timezone.utc) + timedelta(days=2, hours=1)
            ).isoformat(),
        },
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# RSVP
# ---------------------------------------------------------------------------


async def _create_paid_meet(db_session: AsyncSession) -> str:
    # Fixture represents a previously verified and published Open Swim.
    event = Event(
        title="Paid meet",
        event_type="open_swim",
        start_time=datetime.now(timezone.utc) + timedelta(days=2),
        pool_id=uuid.uuid4(),
        pool_fee_kobo=50000,
        created_by=MOCK_MEMBER_ID,
        status="published",
    )
    db_session.add(event)
    await db_session.flush()
    return str(event.id)


@pytest.mark.asyncio
async def test_rsvp_paid_requires_waiver(client, mocks, db_session):
    event_id = await _create_paid_meet(db_session)
    resp = await client.post(
        f"/events/{event_id}/rsvp",
        json={"status": "going", "pay_with_bubbles": True, "waiver_accepted": False},
    )
    assert resp.status_code == 400
    mocks["debit"].assert_not_called()


@pytest.mark.asyncio
async def test_rsvp_maybe_then_going_charges(client, mocks, db_session):
    """Regression: switching maybe → going must charge (was skipped before)."""
    event_id = await _create_paid_meet(db_session)

    r1 = await client.post(f"/events/{event_id}/rsvp", json={"status": "maybe"})
    assert r1.status_code == 200
    mocks["debit"].assert_not_called()  # "maybe" never charges

    r2 = await client.post(
        f"/events/{event_id}/rsvp",
        json={"status": "going", "pay_with_bubbles": True, "waiver_accepted": True},
    )
    assert r2.status_code == 200
    mocks["debit"].assert_awaited_once()  # the transition charges


# ---------------------------------------------------------------------------
# Cancel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_refunds_paid_attendee(client, mocks, db_session):
    event_id = await _create_paid_meet(db_session)
    # A paid "going" RSVP from our member.
    await client.post(
        f"/events/{event_id}/rsvp",
        json={"status": "going", "pay_with_bubbles": True, "waiver_accepted": True},
    )
    mocks["debit"].assert_awaited_once()

    resp = await client.delete(f"/events/open-swim/{event_id}")
    assert resp.status_code == 204
    mocks["credit"].assert_awaited()  # attendee refunded

    # Event is gone.
    missing = await client.get(f"/events/{event_id}")
    assert missing.status_code == 404
