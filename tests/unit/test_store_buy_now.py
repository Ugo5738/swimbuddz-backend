"""Amazon-style Store checkout must preserve a separate multi-item cart."""

import uuid
from decimal import Decimal

from services.store_service.models import Cart, CartStatus, Product, ProductVariant
from services.store_service.routers.orders import _build_quick_buy_cart


def _variant(*, base_price: str, price_override: str | None = None):
    product = Product(
        id=uuid.uuid4(),
        name="Swim goggles",
        slug="swim-goggles",
        base_price_ngn=Decimal(base_price),
    )
    return ProductVariant(
        id=uuid.uuid4(),
        product=product,
        sku="GOGGLE-BLUE",
        price_override_ngn=(
            Decimal(price_override) if price_override is not None else None
        ),
    )


def test_buy_now_preserves_existing_member_cart():
    existing = Cart(member_auth_id="student-1", status=CartStatus.ACTIVE)
    variant = _variant(base_price="15000.00")
    direct = _build_quick_buy_cart("student-1", variant, 2, "club")

    assert direct is not existing
    assert direct.status == CartStatus.CONVERTED
    assert direct.member_auth_id == existing.member_auth_id
    assert existing.status == CartStatus.ACTIVE
    assert existing.items == []
    assert len(direct.items) == 1
    assert direct.items[0].variant_id == variant.id
    assert direct.items[0].quantity == 2
    assert direct.items[0].unit_price_ngn == Decimal("15000.00")
    assert direct.member_discount_percent == Decimal("5")


def test_buy_now_takes_current_variant_price_override():
    variant = _variant(base_price="30000.00", price_override="25000.00")
    direct = _build_quick_buy_cart("student-2", variant, 1, "academy")
    assert direct.items[0].unit_price_ngn == Decimal("25000.00")
    assert direct.member_discount_percent == Decimal("3")


def test_buy_now_unknown_tier_cannot_create_unearned_discount():
    direct = _build_quick_buy_cart(
        "guest-1", _variant(base_price="10000.00"), 1, "unknown"
    )
    assert direct.member_discount_percent == Decimal("0")
