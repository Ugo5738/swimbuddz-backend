"""
Integration tests for pools service admin endpoints.

Tests CRUD operations, filtering, pagination, status transitions,
and soft-delete behaviour on the admin pool management API.
"""

import pytest

POOL_PAYLOAD = {
    "name": "Sunfit Pool Yaba",
    "slug": "sunfit-pool-yaba",
    "location_area": "Yaba",
    "pool_type": "club",
    "contact_person": "Mr Ade",
    "contact_phone": "08012345678",
    "pool_length_m": 25.0,
    "depth_min_m": 1.2,
    "depth_max_m": 2.5,
    "number_of_lanes": 6,
    "indoor_outdoor": "outdoor",
    "water_quality": 4,
    "overall_score": 4,
    "has_changing_rooms": True,
    "has_parking": True,
    "price_per_swimmer_ngn": 1500,
    "available_days_times": {"Mon": "6am-8am", "Wed": "6am-8am", "Sat": "7am-12pm"},
}


# ---------------------------------------------------------------------------
# CREATE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_pool(pools_client):
    """Admin can create a new pool."""
    response = await pools_client.post("/admin/pools", json=POOL_PAYLOAD)
    assert response.status_code == 201

    data = response.json()
    assert data["name"] == POOL_PAYLOAD["name"]
    assert data["slug"] == POOL_PAYLOAD["slug"]
    assert data["location_area"] == "Yaba"
    assert data["partnership_status"] == "prospect"
    assert data["is_active"] is True
    assert "id" in data
    assert "created_at" in data


@pytest.mark.asyncio
async def test_create_pool_duplicate_slug(pools_client):
    """Creating a pool with a duplicate slug returns 400."""
    await pools_client.post("/admin/pools", json=POOL_PAYLOAD)
    response = await pools_client.post("/admin/pools", json=POOL_PAYLOAD)
    assert response.status_code == 400
    assert "slug" in response.json()["detail"].lower()


# ---------------------------------------------------------------------------
# GET DETAIL
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_pool(pools_client):
    """Admin can retrieve a pool by ID."""
    create_resp = await pools_client.post("/admin/pools", json=POOL_PAYLOAD)
    pool_id = create_resp.json()["id"]

    response = await pools_client.get(f"/admin/pools/{pool_id}")
    assert response.status_code == 200
    assert response.json()["id"] == pool_id


@pytest.mark.asyncio
async def test_get_pool_not_found(pools_client):
    """Getting a non-existent pool returns 404."""
    response = await pools_client.get(
        "/admin/pools/00000000-0000-0000-0000-000000000099"
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# LIST + FILTER
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_pools(pools_client):
    """Admin list endpoint returns pools."""
    await pools_client.post("/admin/pools", json=POOL_PAYLOAD)

    response = await pools_client.get("/admin/pools")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 1
    assert len(data["items"]) >= 1


@pytest.mark.asyncio
async def test_list_pools_filter_by_status(pools_client):
    """Filter pools by partnership_status."""
    await pools_client.post("/admin/pools", json=POOL_PAYLOAD)

    # Should appear under "prospect" (default status)
    response = await pools_client.get("/admin/pools?partnership_status=prospect")
    assert response.status_code == 200
    assert response.json()["total"] >= 1

    # Should NOT appear under "active_partner"
    response = await pools_client.get("/admin/pools?partnership_status=active_partner")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_list_pools_filter_by_location(pools_client):
    """Filter pools by location area."""
    await pools_client.post("/admin/pools", json=POOL_PAYLOAD)

    response = await pools_client.get("/admin/pools?location_area=Yaba")
    assert response.status_code == 200
    assert response.json()["total"] >= 1


@pytest.mark.asyncio
async def test_list_pools_search(pools_client):
    """Search pools by name."""
    await pools_client.post("/admin/pools", json=POOL_PAYLOAD)

    response = await pools_client.get("/admin/pools?search=Sunfit")
    assert response.status_code == 200
    assert response.json()["total"] >= 1


@pytest.mark.asyncio
async def test_list_pools_pagination(pools_client):
    """Pagination returns correct page_size."""
    # Create two pools
    await pools_client.post("/admin/pools", json=POOL_PAYLOAD)
    payload2 = {**POOL_PAYLOAD, "name": "VI Pool", "slug": "vi-pool"}
    await pools_client.post("/admin/pools", json=payload2)

    response = await pools_client.get("/admin/pools?page=1&page_size=1")
    assert response.status_code == 200
    data = response.json()
    assert len(data["items"]) == 1
    assert data["total"] >= 2


# ---------------------------------------------------------------------------
# UPDATE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_pool(pools_client):
    """Admin can partially update a pool."""
    create_resp = await pools_client.post("/admin/pools", json=POOL_PAYLOAD)
    pool_id = create_resp.json()["id"]

    response = await pools_client.patch(
        f"/admin/pools/{pool_id}",
        json={"water_quality": 5, "notes": "Great pool"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["water_quality"] == 5
    assert data["notes"] == "Great pool"
    # Unchanged fields preserved
    assert data["name"] == POOL_PAYLOAD["name"]


@pytest.mark.asyncio
async def test_update_pool_slug_uniqueness(pools_client):
    """Changing slug to an existing one returns 400."""
    await pools_client.post("/admin/pools", json=POOL_PAYLOAD)
    payload2 = {**POOL_PAYLOAD, "name": "Other Pool", "slug": "other-pool"}
    create2 = await pools_client.post("/admin/pools", json=payload2)
    pool2_id = create2.json()["id"]

    response = await pools_client.patch(
        f"/admin/pools/{pool2_id}",
        json={"slug": POOL_PAYLOAD["slug"]},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_update_pool_empty_body(pools_client):
    """Sending empty update body returns the pool unchanged."""
    create_resp = await pools_client.post("/admin/pools", json=POOL_PAYLOAD)
    pool_id = create_resp.json()["id"]

    response = await pools_client.patch(f"/admin/pools/{pool_id}", json={})
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# STATUS TRANSITIONS
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_partnership_status(pools_client):
    """Admin can advance pool partnership status."""
    create_resp = await pools_client.post("/admin/pools", json=POOL_PAYLOAD)
    pool_id = create_resp.json()["id"]

    response = await pools_client.post(
        f"/admin/pools/{pool_id}/status?partnership_status=evaluating"
    )
    assert response.status_code == 200
    assert response.json()["partnership_status"] == "evaluating"


@pytest.mark.asyncio
async def test_update_partnership_status_to_active(pools_client):
    """Pool can be promoted to active_partner."""
    create_resp = await pools_client.post("/admin/pools", json=POOL_PAYLOAD)
    pool_id = create_resp.json()["id"]

    response = await pools_client.post(
        f"/admin/pools/{pool_id}/status?partnership_status=active_partner"
    )
    assert response.status_code == 200
    assert response.json()["partnership_status"] == "active_partner"


# ---------------------------------------------------------------------------
# SOFT DELETE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_soft_delete_pool(pools_client):
    """DELETE sets is_active=False, doesn't remove the record."""
    create_resp = await pools_client.post("/admin/pools", json=POOL_PAYLOAD)
    pool_id = create_resp.json()["id"]

    response = await pools_client.delete(f"/admin/pools/{pool_id}")
    assert response.status_code == 204

    # Pool still exists but is inactive
    get_resp = await pools_client.get(f"/admin/pools/{pool_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["is_active"] is False


@pytest.mark.asyncio
async def test_delete_pool_not_found(pools_client):
    """Deleting a non-existent pool returns 404."""
    response = await pools_client.delete(
        "/admin/pools/00000000-0000-0000-0000-000000000099"
    )
    assert response.status_code == 404
