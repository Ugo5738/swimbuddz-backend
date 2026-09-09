import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from services.sessions_service.models import Session


@pytest.fixture(autouse=True)
def current_pool_rates():
    with patch(
        "services.sessions_service.services.club_generation.internal_post",
        new_callable=AsyncMock,
        return_value=SimpleNamespace(
            status_code=200,
            json=lambda: {
                "currency": "NGN",
                "warnings": [],
                "lines": [
                    {
                        "category": "pool",
                        "description": "Current pool rate",
                        "charge_basis": "per_attendee",
                        "unit_cost_naira": 3000,
                        "quantity": 8,
                    }
                ],
            },
        ),
    ):
        yield


@pytest.mark.asyncio
@pytest.mark.integration
async def test_club_template_generation_preserves_pod_type_and_ride_config(
    sessions_client,
    db_session,
):
    club_id = uuid.uuid4()
    pod_id = uuid.uuid4()
    ride_area_id = uuid.uuid4()

    with patch(
        "services.sessions_service.routers.templates.require_valid_club_scope",
        new_callable=AsyncMock,
    ):
        create_response = await sessions_client.post(
            "/sessions/templates",
            json={
                "title": "Dolphins Saturday",
                "session_type": "club",
                "club_access_mode": "active_club",
                "pool_id": str(uuid.uuid4()),
                "pricing_settings": {
                    "pricing_expected_attendees": 8,
                    "margin_value": 500,
                },
                "club_id": str(club_id),
                "pod_id": str(pod_id),
                "location": "sunfit_pool",
                "day_of_week": 5,
                "start_time": "09:00:00",
                "duration_minutes": 120,
                "pool_fee": 2000,
                "ride_share_fee": 1000,
                "capacity": 8,
                "ride_share_config": [
                    {
                        "ride_area_id": str(ride_area_id),
                        "cost": 1000,
                        "capacity": 4,
                    }
                ],
            },
        )
    assert create_response.status_code == 201, create_response.text
    template = create_response.json()
    assert template["session_type"] == "club"
    assert template["club_id"] == str(club_id)
    assert template["pod_id"] == str(pod_id)

    with (
        patch(
            "services.sessions_service.routers.templates.attach_session_ride_configs",
            new_callable=AsyncMock,
            return_value={"created": 1},
        ) as attach_ride_configs,
        patch(
            "services.sessions_service.routers.templates.trigger_session_published_notifications",
            new_callable=AsyncMock,
            return_value=True,
        ) as trigger_notifications,
        patch(
            "services.sessions_service.routers.templates.materialise_opportunities_from_session_template",
            new_callable=AsyncMock,
            return_value={"created_count": 2},
        ),
        patch(
            "services.sessions_service.routers.templates.require_valid_club_scope",
            new_callable=AsyncMock,
        ),
    ):
        generate_response = await sessions_client.post(
            f"/sessions/templates/{template['id']}/generate",
            json={"weeks": 1, "skip_conflicts": True},
        )

    assert generate_response.status_code == 200, generate_response.text
    generated = generate_response.json()
    assert generated["created"] == 1
    assert generated["ride_config_attached"] == 1
    assert generated["volunteer_opportunities_created"] == 2
    assert generated["warnings"] == []

    result = await db_session.execute(
        select(Session).where(Session.template_id == uuid.UUID(template["id"]))
    )
    session = result.scalar_one()
    assert session.session_type.value == "club"
    assert session.club_id == club_id
    assert session.pod_id == pod_id
    assert session.ride_share_fee == 100000
    assert session.pool_fee == 350000
    assert session.club_access_mode == "active_club"
    assert session.status.value == "scheduled"
    assert session.published_at is not None

    attach_ride_configs.assert_awaited_once()
    trigger_notifications.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_template_volunteer_sync_backfills_existing_future_sessions(
    sessions_client,
):
    club_id = uuid.uuid4()
    with patch(
        "services.sessions_service.routers.templates.require_valid_club_scope",
        new_callable=AsyncMock,
    ):
        create_response = await sessions_client.post(
            "/sessions/templates",
            json={
                "title": "Orcas Saturday",
                "session_type": "club",
                "club_access_mode": "active_club",
                "pool_id": str(uuid.uuid4()),
                "pricing_settings": {
                    "pricing_expected_attendees": 8,
                    "margin_value": 500,
                },
                "club_id": str(club_id),
                "location": "sunfit_pool",
                "day_of_week": 5,
                "start_time": "09:00:00",
                "duration_minutes": 120,
                "pool_fee": 2000,
                "capacity": 8,
            },
        )
    assert create_response.status_code == 201, create_response.text
    template = create_response.json()

    with (
        patch(
            "services.sessions_service.routers.templates.trigger_session_published_notifications",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "services.sessions_service.routers.templates.materialise_opportunities_from_session_template",
            new_callable=AsyncMock,
            return_value={"created_count": 0},
        ),
        patch(
            "services.sessions_service.routers.templates.require_valid_club_scope",
            new_callable=AsyncMock,
        ),
    ):
        generate_response = await sessions_client.post(
            f"/sessions/templates/{template['id']}/generate",
            json={"weeks": 1, "skip_conflicts": True},
        )
    assert generate_response.status_code == 200, generate_response.text

    with patch(
        "services.sessions_service.routers.templates.materialise_opportunities_from_session_template",
        new_callable=AsyncMock,
        return_value={"created_count": 1},
    ) as materialise:
        sync_response = await sessions_client.post(
            f"/sessions/templates/{template['id']}/sync-volunteer-opportunities"
        )

    assert sync_response.status_code == 200, sync_response.text
    payload = sync_response.json()
    assert payload["sessions_checked"] == 1
    assert payload["created_count"] == 1
    assert payload["warnings"] == []
    materialise.assert_awaited_once()
