"""Integration tests for store_service catalog routes.

Public catalog endpoints (no auth) + admin product CRUD (admin auth via
the standard test fixture). Covers the common happy paths and a few
validation edges. Not exhaustive — this is the starter skeleton agreed in
the §-loose-ends batch; future commits should extend per-route depth
(variants, images, search/filter combos) once we know which queries the
frontend actually issues.

Scope choices:
  - catalog.py read paths: list/get categories, list/get products
  - admin_catalog/products.py: create / get / update / delete
  - admin_catalog/categories.py: create / update / delete
  - One slug-uniqueness validation test (the only path where a 400 is
    raised in business logic, not by Pydantic)

Not in scope here (worth follow-up):
  - cart.py + checkout.py (mutate Paystack/wallet; needs more mocking)
  - admin_inventory + admin_reports + admin_credits (less customer-facing)
  - Order lifecycle (covered indirectly when /payments tests run)
"""

import uuid
from decimal import Decimal

import pytest


# ---------------------------------------------------------------------------
# In-test factories. We don't promote these to tests/factories.py yet — store
# service is the first to need them, and they're tiny.
# ---------------------------------------------------------------------------


def _make_category(**overrides):
    from services.store_service.models import Category

    suffix = uuid.uuid4().hex[:6]
    defaults = {
        "id": uuid.uuid4(),
        "name": f"Goggles {suffix}",
        "slug": f"goggles-{suffix}",
        "is_active": True,
        "sort_order": 0,
    }
    defaults.update(overrides)
    return Category(**defaults)


def _make_product(**overrides):
    from services.store_service.models import Product, ProductStatus, ProductType

    suffix = uuid.uuid4().hex[:6]
    defaults = {
        "id": uuid.uuid4(),
        "name": f"SwimBuddz Pro Goggles {suffix}",
        "slug": f"pro-goggles-{suffix}",
        "product_type": ProductType.STANDARD,
        "base_price_ngn": Decimal("15000.00"),
        "status": ProductStatus.ACTIVE,
        "is_featured": False,
        "has_variants": False,
    }
    defaults.update(overrides)
    return Product(**defaults)


# ===========================================================================
# Public catalog — categories
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.integration
async def test_list_categories_returns_only_active(store_client, db_session):
    """GET /store/categories must filter out inactive categories."""
    active = _make_category(name="Active Cat")
    inactive = _make_category(name="Inactive Cat", is_active=False)
    db_session.add_all([active, inactive])
    await db_session.commit()

    response = await store_client.get("/store/categories")
    assert response.status_code == 200, response.text
    slugs = {item["slug"] for item in response.json()}
    assert active.slug in slugs
    assert inactive.slug not in slugs


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_category_by_slug_404_on_unknown(store_client):
    response = await store_client.get("/store/categories/totally-fake-slug")
    assert response.status_code == 404
    assert response.json()["detail"] == "Category not found"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_category_by_slug_returns_active_only(store_client, db_session):
    """Inactive categories should not be reachable by slug either."""
    inactive = _make_category(name="Hidden Cat", is_active=False)
    db_session.add(inactive)
    await db_session.commit()

    response = await store_client.get(f"/store/categories/{inactive.slug}")
    assert response.status_code == 404


# ===========================================================================
# Public catalog — products
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.integration
async def test_list_products_returns_only_active(store_client, db_session):
    """Draft / inactive products must not leak via the public list endpoint."""
    from services.store_service.models import ProductStatus

    active = _make_product(name="Active Product")
    draft = _make_product(name="Draft Product", status=ProductStatus.DRAFT)
    db_session.add_all([active, draft])
    await db_session.commit()

    response = await store_client.get("/store/products")
    assert response.status_code == 200
    slugs = {item["slug"] for item in response.json()["items"]}
    assert active.slug in slugs
    assert draft.slug not in slugs


@pytest.mark.asyncio
@pytest.mark.integration
async def test_list_products_pagination(store_client, db_session):
    """page + page_size are honored; total_pages derived from total."""
    products = [_make_product(name=f"Bulk Item {i}") for i in range(5)]
    db_session.add_all(products)
    await db_session.commit()

    response = await store_client.get(
        "/store/products", params={"page": 1, "page_size": 2}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["page"] == 1
    assert body["page_size"] == 2
    assert len(body["items"]) <= 2
    assert body["total"] >= 5
    assert body["total_pages"] >= 3  # ceil(5/2) = 3, plus any pre-existing


@pytest.mark.asyncio
@pytest.mark.integration
async def test_list_products_filters_by_category(store_client, db_session):
    """?category=<slug> joins through store_categories and restricts results."""
    cat_a = _make_category(name="Cat A")
    cat_b = _make_category(name="Cat B")
    db_session.add_all([cat_a, cat_b])
    await db_session.flush()

    in_a = _make_product(name="In A", category_id=cat_a.id)
    in_b = _make_product(name="In B", category_id=cat_b.id)
    db_session.add_all([in_a, in_b])
    await db_session.commit()

    response = await store_client.get(
        "/store/products", params={"category": cat_a.slug}
    )
    assert response.status_code == 200
    slugs = {item["slug"] for item in response.json()["items"]}
    assert in_a.slug in slugs
    assert in_b.slug not in slugs


@pytest.mark.asyncio
@pytest.mark.integration
async def test_list_products_search_substring(store_client, db_session):
    """?search=<term> matches name OR description (ilike)."""
    unique_term = uuid.uuid4().hex[:8]
    matching = _make_product(name=f"GogglesEdition {unique_term}")
    non_matching = _make_product(name="Unrelated Product")
    db_session.add_all([matching, non_matching])
    await db_session.commit()

    response = await store_client.get(
        "/store/products", params={"search": unique_term}
    )
    assert response.status_code == 200
    slugs = {item["slug"] for item in response.json()["items"]}
    assert matching.slug in slugs
    assert non_matching.slug not in slugs


# ===========================================================================
# Admin catalog — product CRUD (admin auth wired by the store_client fixture)
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.integration
async def test_admin_create_product_happy_path(store_client):
    """POST /admin/store/products with a valid payload returns 201."""
    suffix = uuid.uuid4().hex[:6]
    payload = {
        "name": f"Admin Created Goggles {suffix}",
        "slug": f"admin-goggles-{suffix}",
        "base_price_ngn": "12500.00",
        "product_type": "standard",
        "status": "active",
    }
    response = await store_client.post("/admin/store/products", json=payload)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["slug"] == payload["slug"]
    assert body["status"].lower() == "active"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_admin_create_product_rejects_duplicate_slug(store_client, db_session):
    """Slug uniqueness is enforced at the route level with a 400 (not 500)."""
    existing = _make_product()
    db_session.add(existing)
    await db_session.commit()

    payload = {
        "name": "Another Product",
        "slug": existing.slug,  # collision
        "base_price_ngn": "9000.00",
        "product_type": "standard",
    }
    response = await store_client.post("/admin/store/products", json=payload)
    assert response.status_code == 400
    assert "slug" in response.json()["detail"].lower()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_admin_get_product_returns_drafts(store_client, db_session):
    """Admin product detail view includes drafts (vs the public endpoint
    which filters them out).
    """
    from services.store_service.models import ProductStatus

    draft = _make_product(name="Hidden Draft", status=ProductStatus.DRAFT)
    db_session.add(draft)
    await db_session.commit()

    response = await store_client.get(f"/admin/store/products/{draft.id}")
    assert response.status_code == 200, response.text
    assert response.json()["status"].lower() == "draft"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_admin_update_product_patches_fields(store_client, db_session):
    product = _make_product(name="Old Name")
    db_session.add(product)
    await db_session.commit()

    response = await store_client.patch(
        f"/admin/store/products/{product.id}",
        json={"name": "New Name", "is_featured": True},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == "New Name"
    assert body["is_featured"] is True


@pytest.mark.asyncio
@pytest.mark.integration
async def test_admin_delete_product_returns_204(store_client, db_session):
    product = _make_product()
    db_session.add(product)
    await db_session.commit()

    response = await store_client.delete(f"/admin/store/products/{product.id}")
    assert response.status_code == 204


@pytest.mark.asyncio
@pytest.mark.integration
async def test_admin_get_product_404_for_unknown_id(store_client):
    response = await store_client.get(f"/admin/store/products/{uuid.uuid4()}")
    assert response.status_code == 404


# ===========================================================================
# Admin catalog — category CRUD
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.integration
async def test_admin_create_category_happy_path(store_client):
    suffix = uuid.uuid4().hex[:6]
    payload = {
        "name": f"New Category {suffix}",
        "slug": f"new-cat-{suffix}",
    }
    response = await store_client.post(
        "/admin/store/categories", json=payload
    )
    assert response.status_code in (200, 201), response.text
    body = response.json()
    assert body["slug"] == payload["slug"]
