"""Integration tests for store_service cart — the money-path guards.

test_store_catalog.py covers catalog reads + admin product CRUD; this
file covers cart.py, which is where customer money decisions begin
(availability, inventory, totals, discount), plus one focused mixed-tender
checkout test that pins the wallet hold boundary with external calls mocked.

The shared store_client fixture is authenticated as an admin. Tests that pass
``session_id`` still exercise the member-cart branch; anonymous cart ownership
needs a separate unauthenticated fixture and is outside this file's scope.

Scope:
  - add_to_cart: 404 unknown variant, 400 inactive product, 400
    insufficient inventory, happy path
  - get_cart: empty cart shape
  - update_cart_item / remove_cart_item happy paths
  - apply_discount_code: invalid code rejected
  - mixed checkout: no premature hold, then hold persisted before Paystack init

Not in scope (follow-up): payment verification/webhooks, member-tier
auto-discount, coupon stacking, pre-order/dropship inventory bypass.
"""

import uuid
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select


# ---------------------------------------------------------------------------
# Local factories (mirrors test_store_catalog's in-file style).
# ---------------------------------------------------------------------------


def _make_category(**overrides):
    from services.store_service.models import Category

    s = uuid.uuid4().hex[:6]
    d = {
        "id": uuid.uuid4(),
        "name": f"Cat {s}",
        "slug": f"cat-{s}",
        "is_active": True,
        "sort_order": 0,
    }
    d.update(overrides)
    return Category(**d)


def _make_product(**overrides):
    from services.store_service.models import Product, ProductStatus, ProductType

    s = uuid.uuid4().hex[:6]
    d = {
        "id": uuid.uuid4(),
        "name": f"Goggles {s}",
        "slug": f"goggles-{s}",
        "product_type": ProductType.STANDARD,
        "base_price_ngn": Decimal("15000.00"),
        "status": ProductStatus.ACTIVE,
        "is_featured": False,
        "has_variants": False,
    }
    d.update(overrides)
    return Product(**d)


def _make_variant(product_id, **overrides):
    from services.store_service.models import ProductVariant

    s = uuid.uuid4().hex[:6]
    d = {
        "id": uuid.uuid4(),
        "product_id": product_id,
        "sku": f"SKU-{s}",
        "name": "Default",
        "is_active": True,
    }
    d.update(overrides)
    return ProductVariant(**d)


def _make_inventory(variant_id, on_hand=10, reserved=0, **overrides):
    from services.store_service.models import InventoryItem

    d = {
        "id": uuid.uuid4(),
        "variant_id": variant_id,
        "quantity_on_hand": on_hand,
        "quantity_reserved": reserved,
        "low_stock_threshold": 2,
    }
    d.update(overrides)
    return InventoryItem(**d)


async def _seed_buyable(db, *, on_hand=10, status=None):
    """Create category→product→variant→inventory; return the variant."""
    from services.store_service.models import ProductStatus

    cat = _make_category()
    db.add(cat)
    await db.flush()
    prod = _make_product(category_id=cat.id, status=status or ProductStatus.ACTIVE)
    db.add(prod)
    await db.flush()
    variant = _make_variant(prod.id)
    db.add(variant)
    await db.flush()
    db.add(_make_inventory(variant.id, on_hand=on_hand))
    await db.commit()
    return variant


# ---------------------------------------------------------------------------
# add_to_cart guards
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_add_unknown_variant_404(store_client):
    sid = uuid.uuid4().hex
    resp = await store_client.post(
        f"/store/cart/items?session_id={sid}",
        json={"variant_id": str(uuid.uuid4()), "quantity": 1},
    )
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
@pytest.mark.integration
async def test_add_inactive_product_400(store_client, db_session):
    from services.store_service.models import ProductStatus

    variant = await _seed_buyable(db_session, status=ProductStatus.DRAFT)
    sid = uuid.uuid4().hex
    resp = await store_client.post(
        f"/store/cart/items?session_id={sid}",
        json={"variant_id": str(variant.id), "quantity": 1},
    )
    assert resp.status_code == 400, resp.text
    assert "not available" in resp.json()["detail"].lower()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_add_exceeds_inventory_400(store_client, db_session):
    variant = await _seed_buyable(db_session, on_hand=2)
    sid = uuid.uuid4().hex
    resp = await store_client.post(
        f"/store/cart/items?session_id={sid}",
        json={"variant_id": str(variant.id), "quantity": 5},
    )
    assert resp.status_code == 400, resp.text
    assert "available" in resp.json()["detail"].lower()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_add_happy_path_sets_totals(store_client, db_session):
    variant = await _seed_buyable(db_session, on_hand=10)
    sid = uuid.uuid4().hex
    resp = await store_client.post(
        f"/store/cart/items?session_id={sid}",
        json={"variant_id": str(variant.id), "quantity": 2},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["quantity"] == 2
    # base_price 15000 × 2 = 30000 subtotal (CartResponse uses *_ngn names)
    assert Decimal(str(body["subtotal_ngn"])) == Decimal("30000.00")


# ---------------------------------------------------------------------------
# get / update / remove
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_empty_cart(store_client):
    sid = uuid.uuid4().hex
    resp = await store_client.get(f"/store/cart?session_id={sid}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["items"] == []


@pytest.mark.asyncio
@pytest.mark.integration
async def test_update_then_remove_item(store_client, db_session):
    variant = await _seed_buyable(db_session, on_hand=10)
    sid = uuid.uuid4().hex
    add = await store_client.post(
        f"/store/cart/items?session_id={sid}",
        json={"variant_id": str(variant.id), "quantity": 1},
    )
    assert add.status_code == 200, add.text
    item_id = add.json()["items"][0]["id"]

    upd = await store_client.patch(
        f"/store/cart/items/{item_id}?session_id={sid}",
        json={"quantity": 3},
    )
    assert upd.status_code == 200, upd.text
    assert upd.json()["items"][0]["quantity"] == 3

    rm = await store_client.delete(f"/store/cart/items/{item_id}?session_id={sid}")
    assert rm.status_code == 200, rm.text
    assert rm.json()["items"] == []


# ---------------------------------------------------------------------------
# discount
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_apply_invalid_discount_rejected(store_client, db_session):
    variant = await _seed_buyable(db_session, on_hand=10)
    sid = uuid.uuid4().hex
    await store_client.post(
        f"/store/cart/items?session_id={sid}",
        json={"variant_id": str(variant.id), "quantity": 1},
    )
    resp = await store_client.post(
        f"/store/cart/discount?session_id={sid}",
        json={"code": "TOTALLY-FAKE-CODE"},
    )
    # The point: a bad/unverifiable code is NEVER silently applied.
    #  - 400 → payments reachable, code rejected as invalid
    #  - 502 → payments unreachable (test env), endpoint refuses rather
    #          than apply an unvalidated discount (correct degradation)
    # Either way the discount must not stick.
    assert resp.status_code >= 400, resp.text
    cart = await store_client.get(f"/store/cart?session_id={sid}")
    assert cart.json().get("discount_code") in (None, ""), cart.text


# ---------------------------------------------------------------------------
# mixed Bubbles + card checkout
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_mixed_checkout_creates_hold_at_payment_initialization(
    store_client, db_session, monkeypatch
):
    """Cart conversion records Bubbles; provider init reserves them exactly once."""
    import services.store_service.routers.cart as cart_router
    import services.store_service.routers.checkout as checkout_router
    import services.store_service.routers.orders as orders_router
    from services.store_service.models import Order

    member = {
        "id": str(uuid.uuid4()),
        "auth_id": "test-admin-id",
        "email": "admin@admin.com",
        "first_name": "Test",
        "last_name": "Member",
        "phone": None,
        "membership_type": "community",
    }
    member_lookup = AsyncMock(return_value=member)
    start_hold = AsyncMock()
    payment_hold_id = uuid.uuid4()
    payment_hold = AsyncMock(return_value={"id": str(payment_hold_id)})

    async def initialize_payment(*args, **kwargs):
        return {
            "reference": kwargs["reference"],
            "authorization_url": "https://paystack.test/checkout",
            "access_code": "ACCESS-001",
        }

    payment_init = AsyncMock(side_effect=initialize_payment)

    monkeypatch.setattr(cart_router, "get_member_by_auth_id", member_lookup)
    monkeypatch.setattr(orders_router, "get_member_by_auth_id", member_lookup)
    monkeypatch.setattr(orders_router, "create_wallet_hold", start_hold)
    monkeypatch.setattr(
        orders_router, "emit_rewards_event", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(checkout_router, "create_wallet_hold", payment_hold)
    monkeypatch.setattr(checkout_router, "initialize_store_payment", payment_init)

    variant = await _seed_buyable(db_session, on_hand=10)
    add_response = await store_client.post(
        "/store/cart/items",
        json={"variant_id": str(variant.id), "quantity": 1},
    )
    assert add_response.status_code == 200, add_response.text

    checkout_response = await store_client.post(
        "/store/checkout/start",
        json={
            "fulfillment_type": "delivery",
            "delivery_address": {
                "street": "1 Test Street",
                "city": "Lagos",
                "state": "Lagos",
                "phone": "+2348000000000",
            },
            "bubbles_to_apply": 10,
        },
    )
    assert checkout_response.status_code == 200, checkout_response.text
    checkout = checkout_response.json()
    assert checkout["requires_payment"] is True
    assert checkout["bubbles_applied"] == 10
    assert Decimal(str(checkout["paystack_amount_ngn"])) == Decimal("16000.00")
    start_hold.assert_not_awaited()

    order_id = uuid.UUID(checkout["order_id"])
    order = (
        await db_session.execute(select(Order).where(Order.id == order_id))
    ).scalar_one()
    assert order.wallet_hold_id is None
    assert order.payment_reference is None

    payment_response = await store_client.post(
        "/store/checkout/payment",
        json={"order_id": str(order_id)},
    )
    assert payment_response.status_code == 200, payment_response.text
    assert payment_response.json()["authorization_url"] == (
        "https://paystack.test/checkout"
    )

    payment_hold.assert_awaited_once()
    auth_ids = {call.args[0] for call in member_lookup.await_args_list}
    assert len(auth_ids) == 1
    assert payment_hold.await_args.args == (auth_ids.pop(),)
    hold_kwargs = payment_hold.await_args.kwargs
    assert hold_kwargs["amount"] == 10
    assert hold_kwargs["reference_type"] == "store_order"

    payment_init.assert_awaited_once()
    init_kwargs = payment_init.await_args.kwargs
    assert init_kwargs["amount_ngn"] == 16000.0
    assert init_kwargs["bubbles_to_apply"] == 10
    assert init_kwargs["wallet_hold_id"] == str(payment_hold_id)

    await db_session.refresh(order)
    assert order.wallet_hold_id == str(payment_hold_id)
    assert order.payment_reference == payment_response.json()["payment_reference"]
