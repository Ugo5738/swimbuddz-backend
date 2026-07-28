from datetime import datetime, timezone

import pytest

from libs.auth.dependencies import get_optional_user
from services.gateway_service.app import clients
from services.gateway_service.app.main import app
from services.gateway_service.app.tests.stubs import (
    RoutingClient,
    StubUser,
    make_response,
)

RANGE_QUERY = "?from=2026-08-01T00:00:00Z&to=2026-09-01T00:00:00Z"


def _session(
    session_id: str,
    session_type: str,
    *,
    visible: bool = True,
) -> dict:
    return {
        "id": session_id,
        "title": f"{session_type} swim",
        "description": None,
        "session_type": session_type,
        "status": "scheduled",
        "starts_at": datetime(2026, 8, 8, 8, tzinfo=timezone.utc).isoformat(),
        "ends_at": datetime(2026, 8, 8, 10, tzinfo=timezone.utc).isoformat(),
        "timezone": "Africa/Lagos",
        "location_name": "Yaba",
        "pool_id": None,
        "access": {"visible": visible, "bookable": visible},
    }


def _event(event_id: str, audience: str) -> dict:
    return {
        "id": event_id,
        "title": f"{audience} event",
        "description": None,
        "event_type": "social",
        "start_time": datetime(2026, 8, 15, 16, tzinfo=timezone.utc).isoformat(),
        "end_time": None,
        "location": "Lagos",
        "tier_access": audience,
        "pool_id": None,
    }


@pytest.mark.asyncio
async def test_public_calendar_only_returns_community_items(client):
    original_clients = (clients.sessions_client, clients.events_client)
    clients.sessions_client = RoutingClient(
        {
            ("GET", "/sessions/?types=community"): make_response(
                200,
                [
                    _session("community-session", "community"),
                    _session("club-session", "club"),
                ],
            )
        }
    )
    clients.events_client = RoutingClient(
        {
            ("GET", "/events/?upcoming_only=false"): make_response(
                200,
                [
                    _event("community-event", "community"),
                    _event("club-event", "club"),
                ],
            )
        }
    )

    try:
        response = await client.get(f"/api/v1/calendar{RANGE_QUERY}")
    finally:
        clients.sessions_client, clients.events_client = original_clients

    assert response.status_code == 200
    data = response.json()
    assert {item["id"] for item in data["items"]} == {
        "community-session",
        "community-event",
    }
    assert data["available_audiences"] == ["community"]


@pytest.mark.asyncio
async def test_member_calendar_honors_session_access_and_paid_event_tiers(client):
    original_clients = (
        clients.sessions_client,
        clients.events_client,
        clients.members_client,
    )
    app.dependency_overrides[get_optional_user] = lambda: StubUser()
    clients.sessions_client = RoutingClient(
        {
            ("GET", "/sessions/?types=community,club,cohort_class"): make_response(
                200,
                [
                    _session("community-session", "community"),
                    _session("club-session", "club"),
                    _session("private-pod-session", "club", visible=False),
                    _session("academy-session", "cohort_class", visible=False),
                ],
            )
        }
    )
    clients.events_client = RoutingClient(
        {
            ("GET", "/events/?upcoming_only=false"): make_response(
                200,
                [
                    _event("community-event", "community"),
                    _event("club-event", "club"),
                    _event("academy-event", "academy"),
                ],
            )
        }
    )
    clients.members_client = RoutingClient(
        {
            ("GET", "/members/me"): make_response(
                200,
                {
                    "membership": {
                        "effective_paid_tiers": ["club", "community"],
                    }
                },
            )
        }
    )

    try:
        response = await client.get(
            f"/api/v1/calendar{RANGE_QUERY}",
            headers={"Authorization": "Bearer member-token"},
        )
    finally:
        (
            clients.sessions_client,
            clients.events_client,
            clients.members_client,
        ) = original_clients
        app.dependency_overrides.clear()

    assert response.status_code == 200
    data = response.json()
    assert {item["id"] for item in data["items"]} == {
        "community-session",
        "club-session",
        "community-event",
        "club-event",
    }
    assert data["available_audiences"] == ["community", "club"]
