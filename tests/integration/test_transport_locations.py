"""Canonical location links for transport administration."""

import uuid

import pytest

from services.pools_service.models import OperatingArea
from services.transport_service.models import PickupLocation, RideArea


@pytest.mark.asyncio
@pytest.mark.integration
async def test_ride_area_uses_canonical_operating_area(
    transport_client,
    db_session,
):
    operating_area = OperatingArea(
        id=uuid.uuid4(),
        name="Yaba",
        slug="yaba",
        parent_id=None,
        country_code="NG",
        timezone="Africa/Lagos",
        currency="NGN",
        is_active=True,
    )
    db_session.add(operating_area)
    await db_session.commit()

    response = await transport_client.post(
        "/transport/areas",
        json={"operating_area_id": str(operating_area.id)},
    )

    assert response.status_code == 200, response.text
    assert response.json()["operating_area_id"] == str(operating_area.id)
    assert response.json()["name"] == "Yaba"
    assert response.json()["slug"] == "yaba"

    duplicate = await transport_client.post(
        "/transport/areas",
        json={"operating_area_id": str(operating_area.id)},
    )
    assert duplicate.status_code == 409


@pytest.mark.asyncio
@pytest.mark.integration
async def test_route_persists_pool_registry_destination(
    transport_client,
    db_session,
):
    ride_area = RideArea(
        id=uuid.uuid4(),
        name="Yaba",
        slug=f"yaba-{uuid.uuid4().hex[:8]}",
    )
    db_session.add(ride_area)
    await db_session.flush()
    pickup = PickupLocation(
        id=uuid.uuid4(),
        area_id=ride_area.id,
        name="Tejuosho main entrance",
    )
    db_session.add(pickup)
    await db_session.commit()

    pool_id = uuid.uuid4()
    response = await transport_client.post(
        "/transport/routes",
        json={
            "origin_pickup_location_id": str(pickup.id),
            "destination_pool_id": str(pool_id),
            "destination": None,
            "destination_name": "Rowe Park Pool",
            "distance_text": "4 km",
            "duration_text": "15 mins",
            "departure_offset_minutes": 45,
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["destination_pool_id"] == str(pool_id)
    assert response.json()["destination"] is None
