import uuid

import pytest
from libs.auth.dependencies import require_admin
from services.gateway_service.app import clients
from services.gateway_service.app.main import app
from services.gateway_service.app.tests.stubs import (
    RoutingClient,
    StubUser,
    make_response,
)


@pytest.mark.asyncio
async def test_cleanup_hard_mode_handles_204_responses(client):
    member_id = uuid.uuid4()
    auth_id = "auth-123"

    original_clients = {
        "members": clients.members_client,
        "payments": clients.payments_client,
        "attendance": clients.attendance_client,
        "academy": clients.academy_client,
        "communications": clients.communications_client,
        "events": clients.events_client,
        "transport": clients.transport_client,
        "media": clients.media_client,
    }

    app.dependency_overrides[require_admin] = lambda: StubUser(
        role="service_role", email="admin@example.com", token="admin-token"
    )

    clients.members_client = RoutingClient(
        {
            ("GET", f"/members/{member_id}"): make_response(
                200,
                {"id": str(member_id), "auth_id": auth_id},
                "GET",
                f"/members/{member_id}",
            ),
            ("DELETE", f"/members/{member_id}"): make_response(
                204, None, "DELETE", f"/members/{member_id}"
            ),
        }
    )
    clients.payments_client = RoutingClient(
        {
            ("DELETE", f"/payments/admin/members/by-auth/{auth_id}"): make_response(
                204, None, "DELETE", f"/payments/admin/members/by-auth/{auth_id}"
            )
        }
    )
    clients.attendance_client = RoutingClient(
        {
            ("DELETE", f"/attendance/admin/members/{member_id}"): make_response(
                204, None, "DELETE", f"/attendance/admin/members/{member_id}"
            )
        }
    )
    clients.academy_client = RoutingClient(
        {
            ("DELETE", f"/academy/admin/members/{member_id}"): make_response(
                204, None, "DELETE", f"/academy/admin/members/{member_id}"
            )
        }
    )
    clients.communications_client = RoutingClient(
        {
            ("DELETE", f"/admin/members/{member_id}"): make_response(
                204, None, "DELETE", f"/admin/members/{member_id}"
            )
        }
    )
    clients.events_client = RoutingClient(
        {
            ("DELETE", f"/events/admin/members/{member_id}"): make_response(
                204, None, "DELETE", f"/events/admin/members/{member_id}"
            )
        }
    )
    clients.transport_client = RoutingClient(
        {
            ("DELETE", f"/transport/admin/members/{member_id}"): make_response(
                204, None, "DELETE", f"/transport/admin/members/{member_id}"
            )
        }
    )
    clients.media_client = RoutingClient(
        {
            ("DELETE", f"/api/v1/media/admin/members/{member_id}"): make_response(
                204, None, "DELETE", f"/api/v1/media/admin/members/{member_id}"
            )
        }
    )

    try:
        response = await client.post(
            f"/api/v1/admin/cleanup/members/{member_id}",
            json={"mode": "HARD"},
        )
    finally:
        clients.members_client = original_clients["members"]
        clients.payments_client = original_clients["payments"]
        clients.attendance_client = original_clients["attendance"]
        clients.academy_client = original_clients["academy"]
        clients.communications_client = original_clients["communications"]
        clients.events_client = original_clients["events"]
        clients.transport_client = original_clients["transport"]
        clients.media_client = original_clients["media"]
        app.dependency_overrides.clear()

    assert response.status_code == 200
    data = response.json()
    assert data["member_id"] == str(member_id)
    assert data["auth_id"] == auth_id
    assert data["errors"] == []

    # Each downstream call returned 204, so we normalize to {"deleted": 0}
    expected_deleted = {"deleted": 0}
    for service_name in [
        "payments",
        "attendance",
        "academy",
        "communications",
        "events",
        "transport",
        "media",
        "members",
    ]:
        assert data["results"][service_name] == expected_deleted
