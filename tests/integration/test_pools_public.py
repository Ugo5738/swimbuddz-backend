"""
Integration tests for pools service public endpoints.

Public routes only expose active partner pools. These tests verify
that prospect/inactive pools are hidden and that filtering works.
"""

import pytest

POOL_PAYLOAD = {
    "name": "Lekki Community Pool",
    "slug": "lekki-community-pool",
    "location_area": "Lekki",
    "pool_type": "community",
    "pool_length_m": 25.0,
    "number_of_lanes": 4,
    "indoor_outdoor": "outdoor",
    "water_quality": 3,
    "overall_score": 3,
    "has_changing_rooms": True,
    "price_per_swimmer_ngn": 1000,
}


async def _create_active_pool(admin_client, payload=None):
    """Helper: create a pool and promote it to active_partner."""
    payload = payload or POOL_PAYLOAD
    resp = await admin_client.post("/admin/pools", json=payload)
    assert resp.status_code == 201
    pool_id = resp.json()["id"]

    resp = await admin_client.post(
        f"/admin/pools/{pool_id}/status?partnership_status=active_partner"
    )
    assert resp.status_code == 200
    return pool_id


# ---------------------------------------------------------------------------
# LIST (public)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_public_list_only_active_partners(pools_client):
    """Public list endpoint only returns active_partner pools."""
    # Create prospect pool (should NOT appear in public list)
    await pools_client.post("/admin/pools", json=POOL_PAYLOAD)

    # Create active pool (should appear)
    active_payload = {**POOL_PAYLOAD, "name": "Active Pool", "slug": "active-pool"}
    await _create_active_pool(pools_client, active_payload)

    response = await pools_client.get("/pools")
    assert response.status_code == 200
    data = response.json()

    # All returned pools should be active_partner
    for pool in data["items"]:
        assert pool["partnership_status"] == "active_partner"
        assert pool["is_active"] is True


@pytest.mark.asyncio
async def test_public_list_excludes_inactive(pools_client):
    """Soft-deleted active_partner pools are hidden from public."""
    pool_id = await _create_active_pool(pools_client)

    # Soft-delete it
    await pools_client.delete(f"/admin/pools/{pool_id}")

    response = await pools_client.get("/pools")
    assert response.status_code == 200
    pool_ids = [p["id"] for p in response.json()["items"]]
    assert pool_id not in pool_ids


@pytest.mark.asyncio
async def test_public_list_filter_by_type(pools_client):
    """Public list can filter by pool_type."""
    await _create_active_pool(pools_client)

    response = await pools_client.get("/pools?pool_type=community")
    assert response.status_code == 200
    for pool in response.json()["items"]:
        assert pool["pool_type"] == "community"


@pytest.mark.asyncio
async def test_public_list_filter_by_location(pools_client):
    """Public list can filter by location_area."""
    await _create_active_pool(pools_client)

    response = await pools_client.get("/pools?location_area=Lekki")
    assert response.status_code == 200
    assert response.json()["total"] >= 1


@pytest.mark.asyncio
async def test_public_list_search(pools_client):
    """Public list search by name."""
    await _create_active_pool(pools_client)

    response = await pools_client.get("/pools?search=Lekki")
    assert response.status_code == 200
    assert response.json()["total"] >= 1


# ---------------------------------------------------------------------------
# GET DETAIL (public)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_public_get_active_pool(pools_client):
    """Public can view an active_partner pool by ID."""
    pool_id = await _create_active_pool(pools_client)

    response = await pools_client.get(f"/pools/{pool_id}")
    assert response.status_code == 200
    assert response.json()["id"] == pool_id


@pytest.mark.asyncio
async def test_public_get_prospect_pool_404(pools_client):
    """Prospect pools are not visible via public endpoint."""
    resp = await pools_client.post("/admin/pools", json=POOL_PAYLOAD)
    pool_id = resp.json()["id"]

    response = await pools_client.get(f"/pools/{pool_id}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_public_get_inactive_pool_404(pools_client):
    """Soft-deleted active_partner pools are not visible publicly."""
    pool_id = await _create_active_pool(pools_client)

    # Soft-delete
    await pools_client.delete(f"/admin/pools/{pool_id}")

    response = await pools_client.get(f"/pools/{pool_id}")
    assert response.status_code == 404
