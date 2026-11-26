"""Unit tests for ADR-025 session booking & checkout latency optimizations."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from libs.auth.models import AuthUser


@pytest.mark.asyncio
async def test_access_member_payload_uses_single_hop_membership_lookup(monkeypatch):
    """sessions_service resolves member_id + membership in one HTTP call (ADR-025)."""
    from services.sessions_service.routers import member as member_router

    member_id = uuid.uuid4()
    lookup_mock = AsyncMock(
        return_value={
            "member_id": str(member_id),
            "primary_tier": "club",
            "active_tiers": ["club", "community"],
            "community_paid_until": "2035-01-01T00:00:00+00:00",
            "club_paid_until": "2035-01-01T00:00:00+00:00",
            "academy_paid_until": None,
        }
    )
    monkeypatch.setattr(member_router, "get_member_membership_by_auth_id", lookup_mock)

    user = AuthUser(
        user_id="auth-user-1", email="swimmer@example.com", role="authenticated"
    )
    payload = await member_router._access_member_payload_for_user(user)

    lookup_mock.assert_awaited_once_with("auth-user-1", calling_service="sessions")
    assert payload is not None
    assert payload["id"] == str(member_id)
    assert payload["member_id"] == str(member_id)
    assert payload["primary_tier"] == "club"


@pytest.mark.asyncio
async def test_evaluate_session_access_runs_pod_and_club_checks_concurrently(
    monkeypatch,
):
    """Single-session club+pod checks execute concurrently via asyncio.gather (ADR-025)."""
    from services.sessions_service.services import session_access

    pod_started = asyncio.Event()
    club_started = asyncio.Event()

    async def fake_pod(pod_id: str, *, calling_service: str):
        pod_started.set()
        await asyncio.wait_for(club_started.wait(), timeout=1.0)
        return {"active_member_ids": ["member-1"]}

    async def fake_club(checks: list[dict], *, calling_service: str):
        club_started.set()
        await asyncio.wait_for(pod_started.wait(), timeout=1.0)
        return {
            checks[0]["context_key"]: {"allowed": True, "source": "club_enrollment"}
        }

    monkeypatch.setattr(session_access, "get_pod_by_id", fake_pod)
    monkeypatch.setattr(session_access, "check_club_access_batch", fake_club)

    now = datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)
    session = SimpleNamespace(
        id=uuid.uuid4(),
        session_type="club",
        status="scheduled",
        starts_at=now + timedelta(days=1),
        ends_at=now + timedelta(days=1, hours=1),
        cohort_id=None,
        pod_id=uuid.uuid4(),
        club_id=uuid.uuid4(),
        pool_id=uuid.uuid4(),
        event_id=None,
        club_access_mode="plan_included",
        allows_visiting_club_members=False,
    )
    member_payload = {
        "id": "member-1",
        "member_id": "member-1",
        "primary_tier": "club",
        "active_tiers": ["club", "community"],
        "community_paid_until": (now + timedelta(days=30)).isoformat(),
        "club_paid_until": (now + timedelta(days=30)).isoformat(),
        "academy_paid_until": None,
        "post_academy_club_until": None,
    }

    decision = await session_access.evaluate_session_access_for_member(
        session=session,
        member_payload=member_payload,
        now=now,
    )

    assert decision.bookable is True


@pytest.mark.asyncio
async def test_ride_configs_short_circuits_without_sessions_http_when_empty(
    monkeypatch,
):
    """Sessions with zero ride configs return [] with 0 HTTP calls to sessions_service."""
    from services.transport_service.routers import routes

    get_session_mock = AsyncMock()
    monkeypatch.setattr(routes, "get_session_by_id", get_session_mock)

    empty_scalars = SimpleNamespace(all=lambda: [])
    empty_result = SimpleNamespace(scalars=lambda: empty_scalars)
    db = SimpleNamespace(execute=AsyncMock(return_value=empty_result))
    user = AuthUser(
        user_id="auth-user-1", email="swimmer@example.com", role="authenticated"
    )

    result = await routes.get_session_ride_configs(
        session_id=uuid.uuid4(),
        db=db,
        _user=user,
    )

    assert result == []
    get_session_mock.assert_not_awaited()
    assert db.execute.await_count == 1


@pytest.mark.asyncio
async def test_ride_configs_batches_queries_and_computes_availability(monkeypatch):
    """Batched ride-configs query computes availability and pool_id/legacy route ETAs."""
    from services.transport_service.routers import routes

    session_id = uuid.uuid4()
    pool_id = uuid.uuid4()
    area_id = uuid.uuid4()
    cfg_id = uuid.uuid4()
    loc_a_id = uuid.uuid4()
    loc_b_id = uuid.uuid4()
    now = datetime(2026, 11, 28, 9, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(
        routes,
        "get_session_by_id",
        AsyncMock(
            return_value={
                "id": str(session_id),
                "starts_at": now.isoformat(),
                "pool_id": str(pool_id),
                "location": "rowe_park",
            }
        ),
    )

    cfg = SimpleNamespace(
        id=cfg_id,
        session_id=session_id,
        ride_area_id=area_id,
        cost=250_000,
        capacity=4,
        departure_time=None,
        created_at=now,
        updated_at=now,
    )
    area = SimpleNamespace(id=area_id, name="Lekki")
    loc_a = SimpleNamespace(
        id=loc_a_id, area_id=area_id, name="Admiralty", description="Gate 1"
    )
    loc_b = SimpleNamespace(
        id=loc_b_id, area_id=area_id, name="Chevron", description=None
    )
    route_pool = SimpleNamespace(
        origin_pickup_location_id=loc_a_id,
        destination_pool_id=pool_id,
        destination=None,
        distance_text="14 km",
        duration_text="35 mins",
        departure_offset_minutes=60,
    )
    route_legacy = SimpleNamespace(
        origin_pickup_location_id=loc_b_id,
        destination_pool_id=None,
        destination="rowe_park",
        distance_text="18 km",
        duration_text="45 mins",
        departure_offset_minutes=90,
    )

    query_results = [
        SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [cfg])),
        SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [area])),
        SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [loc_a, loc_b])),
        SimpleNamespace(all=lambda: [(cfg_id, loc_a_id, 2)]),
        SimpleNamespace(
            scalars=lambda: SimpleNamespace(all=lambda: [route_pool, route_legacy])
        ),
    ]
    db = SimpleNamespace(execute=AsyncMock(side_effect=query_results))
    user = AuthUser(
        user_id="auth-user-1", email="swimmer@example.com", role="authenticated"
    )

    result = await routes.get_session_ride_configs(
        session_id=session_id,
        db=db,
        _user=user,
    )

    # Constant 5 SQL queries regardless of area/location count
    assert db.execute.await_count == 5
    assert len(result) == 1
    assert result[0].ride_area_name == "Lekki"
    assert result[0].cost == 2500.0
    locs = {loc["name"]: loc for loc in result[0].pickup_locations}
    assert locs["Admiralty"]["is_available"] is True
    assert locs["Admiralty"]["current_bookings"] == 2
    assert locs["Admiralty"]["distance_text"] == "14 km"
    assert locs["Admiralty"]["departure_time_calculated"] == now - timedelta(minutes=60)
    # Second location locked because Admiralty already has active bookings
    assert locs["Chevron"]["is_available"] is False
    assert locs["Chevron"]["distance_text"] == "18 km"
    assert locs["Chevron"]["departure_time_calculated"] == now - timedelta(minutes=90)


@pytest.mark.asyncio
async def test_get_member_by_auth_skips_media_service_when_include_photo_false(
    monkeypatch,
):
    """include_photo=False skips media_service photo URL resolution (ADR-025)."""
    from services.members_service.routers.internal import lookups

    resolve_mock = AsyncMock(return_value={})
    monkeypatch.setattr(lookups, "resolve_media_urls", resolve_mock)

    member = SimpleNamespace(
        id=uuid.uuid4(),
        auth_id="auth-1",
        first_name="Ada",
        last_name="Swimmer",
        email="ada@example.com",
        profile=SimpleNamespace(phone="+2348000000000"),
        profile_photo_media_id=uuid.uuid4(),
    )
    db = SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(scalar_one_or_none=lambda: member)
        )
    )
    svc = AuthUser(user_id="svc", email="svc@swimbuddz.internal", role="service_role")

    result = await lookups.get_member_by_auth_id(
        auth_id="auth-1",
        include_photo=False,
        _=svc,
        db=db,
    )

    resolve_mock.assert_not_awaited()
    assert result.profile_photo_url is None
    assert result.id == str(member.id)


@pytest.mark.asyncio
async def test_get_member_membership_by_auth_id_single_query(monkeypatch):
    """GET /internal/members/by-auth/{auth_id}/membership resolves member + membership in 1 call."""
    from services.members_service.routers.internal import (
        lookups,
        membership as membership_mod,
    )

    monkeypatch.setattr(
        membership_mod, "has_current_club_access", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(
        lookups, "has_current_club_access", AsyncMock(return_value=False)
    )

    member_id = uuid.uuid4()
    future = datetime(2035, 1, 1, tzinfo=timezone.utc)
    membership_row = SimpleNamespace(
        member_id=member_id,
        primary_tier="club",
        active_tiers=["club", "community"],
        declared_tiers=["club", "community"],
        requested_tiers=[],
        community_paid_until=future,
        club_paid_until=future,
        academy_paid_until=None,
        post_academy_club_until=None,
        pending_payment_reference=None,
        pending_tier_payments={},
    )
    member = SimpleNamespace(id=member_id, auth_id="auth-1", membership=membership_row)
    db = SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(scalar_one_or_none=lambda: member)
        ),
        commit=AsyncMock(),
    )
    svc = AuthUser(user_id="svc", email="svc@swimbuddz.internal", role="service_role")

    result = await lookups.get_member_membership_by_auth_id(
        auth_id="auth-1",
        _=svc,
        db=db,
    )

    assert result.member_id == str(member_id)
    assert result.primary_tier == "club"
    assert "club" in result.active_tiers
    assert "community" in result.active_tiers
    assert db.execute.await_count == 1
