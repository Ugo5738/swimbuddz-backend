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
# Create
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_free_meet_no_pool(client, mocks):
    resp = await client.post(
        "/events/open-swim",
        json={"title": "Beach swim", "start_time": _future(), "location": "Tarkwa Bay"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["event_type"] == "open_swim"
    assert body["pool_id"] is None
    assert body["total_cost_naira"] is None  # free
    mocks["pool"].assert_not_called()  # no pool lookup for a free meet


@pytest.mark.asyncio
async def test_create_paid_pool_meet_snapshots_fee_and_caps_capacity(client, mocks):
    pool_id = str(uuid.uuid4())
    mocks["pool"].return_value["id"] = pool_id
    resp = await client.post(
        "/events/open-swim",
        json={
            "title": "Saturday laps",
            "start_time": _future(),
            "pool_id": pool_id,
            "organizer_surcharge_naira": 200,
            "max_capacity": 50,  # exceeds pool max (20) → should cap
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["pool_fee_naira"] == 500.0  # snapshotted per-swimmer fee
    assert body["organizer_surcharge_naira"] == 200.0
    assert body["total_cost_naira"] == 700.0  # fee + surcharge
    assert body["max_capacity"] == 20  # capped at pool's physical max


@pytest.mark.asyncio
async def test_create_rejects_minor(client, mocks):
    mocks["member"].return_value["date_of_birth"] = (
        datetime.now(timezone.utc) - timedelta(days=365 * 12)
    ).isoformat()
    resp = await client.post(
        "/events/open-swim", json={"title": "Kid swim", "start_time": _future()}
    )
    assert resp.status_code == 403
    assert "18" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_create_rejects_flat_fee_pool(client, mocks):
    mocks["pool"].return_value["flat_session_fee_ngn"] = "10000"
    resp = await client.post(
        "/events/open-swim",
        json={
            "title": "Flat pool",
            "start_time": _future(),
            "pool_id": str(uuid.uuid4()),
        },
    )
    assert resp.status_code == 400
    assert "flat" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_create_quota_enforced(client, mocks, db_session: AsyncSession):
    # Seed 3 upcoming open-swims hosted by this member (the cap).
    for i in range(3):
        db_session.add(
            Event(
                title=f"Existing {i}",
                event_type="open_swim",
                start_time=datetime.now(timezone.utc) + timedelta(days=3 + i),
                created_by=MOCK_MEMBER_ID,
            )
        )
    await db_session.flush()

    resp = await client.post(
        "/events/open-swim", json={"title": "One too many", "start_time": _future()}
    )
    assert resp.status_code == 429


# ---------------------------------------------------------------------------
# RSVP
# ---------------------------------------------------------------------------


async def _create_paid_meet(client, mocks) -> str:
    resp = await client.post(
        "/events/open-swim",
        json={
            "title": "Paid meet",
            "start_time": _future(),
            "pool_id": str(uuid.uuid4()),
            "organizer_surcharge_naira": 0,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


@pytest.mark.asyncio
async def test_rsvp_paid_requires_waiver(client, mocks):
    event_id = await _create_paid_meet(client, mocks)
    resp = await client.post(
        f"/events/{event_id}/rsvp",
        json={"status": "going", "pay_with_bubbles": True, "waiver_accepted": False},
    )
    assert resp.status_code == 400
    mocks["debit"].assert_not_called()


@pytest.mark.asyncio
async def test_rsvp_maybe_then_going_charges(client, mocks):
    """Regression: switching maybe → going must charge (was skipped before)."""
    event_id = await _create_paid_meet(client, mocks)

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
async def test_cancel_refunds_paid_attendee(client, mocks):
    event_id = await _create_paid_meet(client, mocks)
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
