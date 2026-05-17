"""Integration tests for transport_service — the ride-booking money path.

A ride booking carries a fare (``SessionRideConfig.cost``, kobo) and a
capacity-driven van assignment. The guards worth pinning:

  - cost is computed as ``cost_kobo * num_seats / 100`` naira (a wrong
    divisor or a missing ``* num_seats`` over/under-charges riders)
  - a missing ride config is a clean 404, not a 500
  - re-POSTing as the same member *updates* the existing booking (no
    duplicate row, no second charge) — the endpoint is idempotent per
    (session, member)
  - van numbering rolls over by capacity (seats // capacity + 1) so a
    full van pushes the next rider to the next van

auth resolves via the local ``members`` table (MemberRef.auth_id ==
JWT.user_id) — no cross-service HTTP — so we seed a real Member with a
known auth_id and point the app's auth deps at it (the
test_members_pods pattern). Chat-channel sync is patched out; chat
downtime must never block a booking and tests shouldn't need it.

Not in scope (follow-up): pay_with_bubbles wallet debit + the
402/403/502 wallet-error mapping (belongs with the wallet suite), the
service-role member_id override path, area/route admin CRUD.
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from libs.auth.dependencies import (
    get_current_user,
    get_optional_user,
    require_admin,
    require_service_role,
)

from tests.conftest import make_admin_user
from tests.factories import MemberFactory

# Chat-sync calls live in the bookings router's namespace (patch where
# they're called from — MEMORY.md).
_ENSURE = "services.transport_service.routers.bookings.ensure_trip_channel"
_RECONCILE = "services.transport_service.routers.bookings.reconcile_trip_membership"


def _silence_chat_sync():
    return (
        patch(_ENSURE, new_callable=AsyncMock, return_value=None),
        patch(_RECONCILE, new_callable=AsyncMock, return_value=None),
    )


async def _setup_member(db_session):
    """Seed a Member with a known auth_id and point the transport app's
    auth deps at it. Returns the seeded Member."""
    unique = uuid.uuid4().hex[:8]
    user = make_admin_user(
        user_id=str(uuid.uuid4()), email=f"rider-{unique}@test.com"
    )
    member = MemberFactory.create(auth_id=user.user_id, email=user.email)
    db_session.add(member)
    await db_session.commit()

    from services.transport_service.app.main import app

    async def _get_user():
        return user

    app.dependency_overrides[get_current_user] = _get_user
    app.dependency_overrides[get_optional_user] = _get_user
    app.dependency_overrides[require_admin] = _get_user
    app.dependency_overrides[require_service_role] = _get_user
    return member


# ---------------------------------------------------------------------------
# Local factories
# ---------------------------------------------------------------------------


def _make_area(**overrides):
    from services.transport_service.models import RideArea

    s = uuid.uuid4().hex[:6]
    d = {"id": uuid.uuid4(), "name": f"Area {s}", "slug": f"area-{s}"}
    d.update(overrides)
    return RideArea(**d)


def _make_pickup(area_id, **overrides):
    from services.transport_service.models import PickupLocation

    s = uuid.uuid4().hex[:6]
    d = {"id": uuid.uuid4(), "name": f"Stop {s}", "area_id": area_id}
    d.update(overrides)
    return PickupLocation(**d)


def _make_config(area_id, *, cost=0, capacity=4, **overrides):
    """cost is kobo (integer), per the model contract."""
    from services.transport_service.models import SessionRideConfig

    d = {
        "id": uuid.uuid4(),
        "session_id": uuid.uuid4(),
        "ride_area_id": area_id,
        "cost": cost,
        "capacity": capacity,
        "departure_time": datetime(2026, 6, 1, 7, 0, tzinfo=timezone.utc),
    }
    d.update(overrides)
    return SessionRideConfig(**d)


async def _seed_ride(db_session, *, cost=0, capacity=4):
    """area → pickup → config; return (config, pickup)."""
    area = _make_area()
    db_session.add(area)
    await db_session.flush()
    pickup = _make_pickup(area.id)
    cfg = _make_config(area.id, cost=cost, capacity=capacity)
    db_session.add_all([pickup, cfg])
    await db_session.commit()
    return cfg, pickup


# ---------------------------------------------------------------------------
# create_ride_booking guards
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_book_unknown_config_404(transport_client, db_session):
    await _setup_member(db_session)
    area = _make_area()
    db_session.add(area)
    await db_session.flush()
    pickup = _make_pickup(area.id)
    db_session.add(pickup)
    await db_session.commit()

    e, r = _silence_chat_sync()
    with e, r:
        resp = await transport_client.post(
            f"/transport/sessions/{uuid.uuid4()}/bookings",
            json={
                "session_ride_config_id": str(uuid.uuid4()),
                "pickup_location_id": str(pickup.id),
            },
        )
    assert resp.status_code == 404, resp.text
    assert "config" in resp.json()["detail"].lower()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_book_free_ride_happy_path(transport_client, db_session):
    await _setup_member(db_session)
    cfg, pickup = await _seed_ride(db_session, cost=0, capacity=4)

    e, r = _silence_chat_sync()
    with e, r:
        resp = await transport_client.post(
            f"/transport/sessions/{cfg.session_id}/bookings",
            json={
                "session_ride_config_id": str(cfg.id),
                "pickup_location_id": str(pickup.id),
            },
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["cost"] == 0.0
    assert body["num_seats"] == 1
    assert body["assigned_ride_number"] == 1


@pytest.mark.asyncio
@pytest.mark.integration
async def test_multi_seat_cost_is_kobo_times_seats(transport_client, db_session):
    """₦2,000 (200000 kobo) × 3 seats → 6000.0 naira on the response."""
    await _setup_member(db_session)
    cfg, pickup = await _seed_ride(db_session, cost=200000, capacity=10)

    e, r = _silence_chat_sync()
    with e, r:
        resp = await transport_client.post(
            f"/transport/sessions/{cfg.session_id}/bookings",
            json={
                "session_ride_config_id": str(cfg.id),
                "pickup_location_id": str(pickup.id),
                "num_seats": 3,
            },
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["cost"] == 6000.0


@pytest.mark.asyncio
@pytest.mark.integration
async def test_rebooking_updates_not_duplicates(transport_client, db_session):
    """Same member re-POSTs with a different pickup: one row, updated,
    no second charge (the update branch never debits)."""
    from sqlalchemy import func, select

    from services.transport_service.models import RideBooking

    await _setup_member(db_session)
    cfg, pickup1 = await _seed_ride(db_session, cost=50000, capacity=4)
    # Capture scalars before any expire — re-reading an expired ORM
    # attribute synchronously triggers IO outside the async greenlet.
    session_id = cfg.session_id
    cfg_id = cfg.id
    pickup2 = _make_pickup(cfg.ride_area_id)
    db_session.add(pickup2)
    await db_session.commit()

    e, r = _silence_chat_sync()
    with e, r:
        first = await transport_client.post(
            f"/transport/sessions/{session_id}/bookings",
            json={
                "session_ride_config_id": str(cfg_id),
                "pickup_location_id": str(pickup1.id),
            },
        )
        assert first.status_code == 200, first.text
        second = await transport_client.post(
            f"/transport/sessions/{session_id}/bookings",
            json={
                "session_ride_config_id": str(cfg_id),
                "pickup_location_id": str(pickup2.id),
            },
        )
    assert second.status_code == 200, second.text
    assert second.json()["pickup_location_id"] == str(pickup2.id)

    count = (
        await db_session.execute(
            select(func.count())
            .select_from(RideBooking)
            .where(RideBooking.session_id == session_id)
        )
    ).scalar_one()
    assert count == 1


@pytest.mark.asyncio
@pytest.mark.integration
async def test_van_number_rolls_over_by_capacity(transport_client, db_session):
    """capacity=2: rider A books 2 seats (fills van 1) at a pickup; a
    second member booking the same pickup lands in van 2."""
    cfg, pickup = await _seed_ride(db_session, cost=0, capacity=2)
    session_id = cfg.session_id

    # Rider A — 2 seats fill van 1.
    await _setup_member(db_session)
    e, r = _silence_chat_sync()
    with e, r:
        a = await transport_client.post(
            f"/transport/sessions/{session_id}/bookings",
            json={
                "session_ride_config_id": str(cfg.id),
                "pickup_location_id": str(pickup.id),
                "num_seats": 2,
            },
        )
    assert a.status_code == 200, a.text
    assert a.json()["assigned_ride_number"] == 1

    # Rider B — same pickup, van 1 is full → van 2.
    await _setup_member(db_session)
    e, r = _silence_chat_sync()
    with e, r:
        b = await transport_client.post(
            f"/transport/sessions/{session_id}/bookings",
            json={
                "session_ride_config_id": str(cfg.id),
                "pickup_location_id": str(pickup.id),
            },
        )
    assert b.status_code == 200, b.text
    assert b.json()["assigned_ride_number"] == 2


# ---------------------------------------------------------------------------
# read endpoints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_my_booking_none_then_present(transport_client, db_session):
    await _setup_member(db_session)
    cfg, pickup = await _seed_ride(db_session, cost=0, capacity=4)

    none = await transport_client.get(
        f"/transport/sessions/{cfg.session_id}/bookings/me"
    )
    assert none.status_code == 200, none.text
    assert none.json() is None

    e, r = _silence_chat_sync()
    with e, r:
        await transport_client.post(
            f"/transport/sessions/{cfg.session_id}/bookings",
            json={
                "session_ride_config_id": str(cfg.id),
                "pickup_location_id": str(pickup.id),
            },
        )
    mine = await transport_client.get(
        f"/transport/sessions/{cfg.session_id}/bookings/me"
    )
    assert mine.status_code == 200, mine.text
    assert mine.json() is not None
    assert mine.json()["session_id"] == str(cfg.session_id)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_list_session_bookings(transport_client, db_session):
    await _setup_member(db_session)
    cfg, pickup = await _seed_ride(db_session, cost=0, capacity=4)

    e, r = _silence_chat_sync()
    with e, r:
        await transport_client.post(
            f"/transport/sessions/{cfg.session_id}/bookings",
            json={
                "session_ride_config_id": str(cfg.id),
                "pickup_location_id": str(pickup.id),
            },
        )
    resp = await transport_client.get(
        f"/transport/sessions/{cfg.session_id}/bookings"
    )
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["session_id"] == str(cfg.session_id)
