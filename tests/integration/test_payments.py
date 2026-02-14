"""Integration tests for payments_service endpoints."""

import uuid

import pytest
from tests.factories import DiscountFactory, MemberFactory

# ---------------------------------------------------------------------------
# Discount CRUD (admin auth)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_create_discount(payments_client, db_session):
    """Admin can create a discount code."""
    payload = {
        "code": f"TEST-{uuid.uuid4().hex[:5].upper()}",
        "discount_type": "percentage",
        "value": 25.0,
        "max_uses": 100,
        "applies_to": ["COMMUNITY"],
        "is_active": True,
    }
    response = await payments_client.post(
        "/payments/admin/discounts",
        json=payload,
    )

    assert response.status_code in (200, 201), response.text
    data = response.json()
    assert data["code"] == payload["code"]
    assert data["value"] == 25.0


@pytest.mark.asyncio
@pytest.mark.integration
async def test_list_discounts(payments_client, db_session):
    """Admin can list all discounts."""
    d = DiscountFactory.create()
    db_session.add(d)
    await db_session.commit()

    response = await payments_client.get("/payments/admin/discounts")

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_discount_by_id(payments_client, db_session):
    """Admin can get a specific discount."""
    d = DiscountFactory.create()
    db_session.add(d)
    await db_session.commit()

    response = await payments_client.get(f"/payments/admin/discounts/{d.id}")

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == str(d.id)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_update_discount(payments_client, db_session):
    """Admin can update a discount."""
    d = DiscountFactory.create()
    db_session.add(d)
    await db_session.commit()

    response = await payments_client.patch(
        f"/payments/admin/discounts/{d.id}",
        json={"value": 50.0},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["value"] == 50.0


@pytest.mark.asyncio
@pytest.mark.integration
async def test_delete_discount(payments_client, db_session):
    """Admin can delete a discount."""
    d = DiscountFactory.create()
    db_session.add(d)
    await db_session.commit()

    response = await payments_client.delete(f"/payments/admin/discounts/{d.id}")

    assert response.status_code in (200, 204)
