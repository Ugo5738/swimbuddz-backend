import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from services.sessions_service.models import Session


@pytest.mark.asyncio
@pytest.mark.integration
async def test_club_template_generation_preserves_pod_type_and_ride_config(
    sessions_client,
    db_session,
):
    pod_id = uuid.uuid4()
    ride_area_id = uuid.uuid4()

    create_response = await sessions_client.post(
        "/sessions/templates",
        json={
            "title": "Dolphins Saturday",
            "session_type": "club",
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
    assert session.pod_id == pod_id
    assert session.ride_share_fee == 100000
    assert session.status.value == "scheduled"
    assert session.published_at is not None

    attach_ride_configs.assert_awaited_once()
    trigger_notifications.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_template_volunteer_sync_backfills_existing_future_sessions(sessions_client):
    create_response = await sessions_client.post(
        "/sessions/templates",
        json={
            "title": "Orcas Saturday",
            "session_type": "club",
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
