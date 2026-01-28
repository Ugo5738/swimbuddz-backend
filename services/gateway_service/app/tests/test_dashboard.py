import uuid
from datetime import datetime, timedelta

import pytest
from libs.auth.dependencies import get_current_user, require_admin
from services.gateway_service.app import clients
from services.gateway_service.app.main import app
from services.gateway_service.app.tests.stubs import (
    RoutingClient,
    StubUser,
    make_response,
)


@pytest.mark.asyncio
async def test_member_dashboard_aggregates_service_responses(client):
    now = datetime.utcnow()
    member_id = uuid.uuid4()

    member_payload = {
        "id": str(member_id),
        "auth_id": "auth-123",
        "email": "user@example.com",
        "first_name": "Test",
        "last_name": "User",
        "is_active": True,
        "registration_complete": True,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }
    sessions_payload = [
        {
            "id": str(uuid.uuid4()),
            "title": "Morning Swim",
            "description": "Pool laps",
            "location": "sunfit_pool",
            "type": "club",
            "pool_fee": 500.0,
            "ride_share_fee": 0.0,
            "capacity": 10,
            "start_time": now.isoformat(),
            "end_time": (now + timedelta(hours=1)).isoformat(),
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "template_id": None,
            "cohort_id": None,
            "is_recurring_instance": False,
        }
    ]
    announcements_payload = [
        {
            "id": str(uuid.uuid4()),
            "title": "Welcome",
            "summary": "Hello",
            "body": "Welcome to SwimBuddz",
            "category": "general",
            "is_pinned": False,
            "published_at": now.isoformat(),
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }
    ]

    original_clients = (
        clients.members_client,
        clients.sessions_client,
        clients.communications_client,
    )
    app.dependency_overrides[get_current_user] = lambda: StubUser(token="bearer-token")

    clients.members_client = RoutingClient(
        {
            ("GET", "/members/me"): make_response(
                200, member_payload, "GET", "/members/me"
            )
        }
    )
    clients.sessions_client = RoutingClient(
        {
            ("GET", "/sessions/"): make_response(
                200, sessions_payload, "GET", "/sessions/"
            )
        }
    )
    clients.communications_client = RoutingClient(
        {
            ("GET", "/announcements/"): make_response(
                200, announcements_payload, "GET", "/announcements/"
            )
        }
    )

    try:
        response = await client.get("/api/v1/me/dashboard")
    finally:
        (
            clients.members_client,
            clients.sessions_client,
            clients.communications_client,
        ) = original_clients
        app.dependency_overrides.clear()

    assert response.status_code == 200
    data = response.json()
    assert data["member"]["email"] == member_payload["email"]
    assert len(data["upcoming_sessions"]) == 1
    assert data["upcoming_sessions"][0]["title"] == sessions_payload[0]["title"]
    assert len(data["latest_announcements"]) == 1
    assert data["latest_announcements"][0]["title"] == announcements_payload[0]["title"]


@pytest.mark.asyncio
async def test_admin_dashboard_stats_aggregates_service_responses(client):
    member_stats = {
        "total_members": 3,
        "active_members": 2,
        "approved_members": 1,
        "pending_approvals": 1,
    }
    session_stats = {"upcoming_sessions_count": 4}
    announcement_stats = {"recent_announcements_count": 2}

    original_clients = (
        clients.members_client,
        clients.sessions_client,
        clients.communications_client,
    )
    app.dependency_overrides[require_admin] = lambda: StubUser(
        role="service_role", email="admin@example.com", token="admin-token"
    )

    clients.members_client = RoutingClient(
        {
            ("GET", "/members/stats"): make_response(
                200, member_stats, "GET", "/members/stats"
            )
        }
    )
    clients.sessions_client = RoutingClient(
        {
            ("GET", "/sessions/stats"): make_response(
                200, session_stats, "GET", "/sessions/stats"
            )
        }
    )
    clients.communications_client = RoutingClient(
        {
            ("GET", "/announcements/stats"): make_response(
                200, announcement_stats, "GET", "/announcements/stats"
            )
        }
    )

    try:
        response = await client.get("/api/v1/admin/dashboard-stats")
    finally:
        (
            clients.members_client,
            clients.sessions_client,
            clients.communications_client,
        ) = original_clients
        app.dependency_overrides.clear()

    assert response.status_code == 200
    data = response.json()
    assert data["total_members"] == member_stats["total_members"]
    assert data["active_members"] == member_stats["active_members"]
    assert data["approved_members"] == member_stats["approved_members"]
    assert data["pending_approvals"] == member_stats["pending_approvals"]
    assert data["upcoming_sessions_count"] == session_stats["upcoming_sessions_count"]
    assert (
        data["recent_announcements_count"]
        == announcement_stats["recent_announcements_count"]
    )
