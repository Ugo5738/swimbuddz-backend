from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from services.store_service.schemas.main.product import (
    AdminProductVariantResponse,
    ProductDetail,
    ProductResponse,
    PublicProductDetail,
    PublicProductVariantInfo,
)


def test_public_serialization_drops_internal_cost_even_when_source_contains_it():
    now = datetime.now(timezone.utc)
    product = dict(
        id=uuid4(),
        name="Goggles",
        slug="goggles",
        base_price_ngn=15000,
        cost_price_ngn=Decimal("7000.00"),
        created_at=now,
        updated_at=now,
    )
    variant = dict(
        id=uuid4(),
        product_id=product["id"],
        sku="GOGGLES-BLUE",
        cost_price_ngn=Decimal("4500.00"),
        created_at=now,
        updated_at=now,
    )
    assert "cost_price_ngn" not in ProductResponse.model_validate(product).model_dump()
    public = PublicProductDetail.model_validate(
        {**product, "variants": [variant]}
    ).model_dump()
    assert "cost_price_ngn" not in public
    assert "cost_price_ngn" not in public["variants"][0]
    assert (
        "cost_price_ngn"
        not in PublicProductVariantInfo.model_json_schema()["properties"]
    )
    admin = ProductDetail.model_validate({**product, "variants": [variant]})
    assert admin.cost_price_ngn == Decimal("7000.00")
    assert admin.variants[0].cost_price_ngn == Decimal("4500.00")
    assert AdminProductVariantResponse.model_validate(
        variant
    ).cost_price_ngn == Decimal("4500.00")
