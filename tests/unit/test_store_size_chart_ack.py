"""Tests for store size chart acknowledgment schemas and cart enrichment."""

from decimal import Decimal
import uuid

from services.store_service.schemas.main.cart import CartItemResponse, CartResponse
from services.store_service.schemas.main.checkout import CheckoutStartRequest


def test_cart_item_response_schema_size_chart_defaults():
    """Verify CartItemResponse defaults requires_size_chart_ack to False and size_chart_url to None."""
    item = CartItemResponse(
        id=uuid.uuid4(),
        variant_id=uuid.uuid4(),
        quantity=1,
        unit_price_ngn=Decimal("15000"),
        product_name="Swim Goggles",
    )
    assert item.requires_size_chart_ack is False
    assert item.size_chart_url is None

    swimwear_item = CartItemResponse(
        id=uuid.uuid4(),
        variant_id=uuid.uuid4(),
        quantity=1,
        unit_price_ngn=Decimal("30000"),
        product_name="Full Coverage 2-Piece Swim Set",
        requires_size_chart_ack=True,
        size_chart_url="https://example.com/size-chart",
    )
    assert swimwear_item.requires_size_chart_ack is True
    assert swimwear_item.size_chart_url == "https://example.com/size-chart"


def test_cart_response_schema_has_size_chart_field():
    """Verify CartResponse includes requires_size_chart_ack."""
    from services.store_service.models import CartStatus
    from datetime import datetime, timezone

    cart = CartResponse(
        id=uuid.uuid4(),
        status=CartStatus.ACTIVE,
        discount_code=None,
        member_discount_percent=None,
        expires_at=None,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        items=[],
        requires_size_chart_ack=True,
        subtotal_ngn=Decimal("30000"),
        discount_amount_ngn=Decimal("0"),
        total_ngn=Decimal("30000"),
    )
    assert cart.requires_size_chart_ack is True


def test_checkout_start_request_size_chart_acknowledged():
    """Verify CheckoutStartRequest accepts size_chart_acknowledged."""
    req_default = CheckoutStartRequest()
    assert req_default.size_chart_acknowledged is False

    req_acked = CheckoutStartRequest(size_chart_acknowledged=True)
    assert req_acked.size_chart_acknowledged is True
