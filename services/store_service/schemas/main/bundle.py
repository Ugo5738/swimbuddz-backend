"""Bundle / kit schemas (composite products)."""

import uuid
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from services.store_service.models import ProductType

from .product import ProductCreate, ProductDetail, ProductUpdate


class BundleItemCreate(BaseModel):
    """Add a component product to a bundle."""

    component_product_id: uuid.UUID
    component_variant_id: Optional[uuid.UUID] = None
    quantity: int = Field(1, ge=1)
    sort_order: int = 0


class BundleItemResponse(BaseModel):
    """Bundle component with resolved product info."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    component_product_id: uuid.UUID
    component_variant_id: Optional[uuid.UUID] = None
    quantity: int
    sort_order: int

    # Resolved from relationships
    component_name: Optional[str] = None
    component_slug: Optional[str] = None
    component_image_url: Optional[str] = None
    component_price_ngn: Optional[Decimal] = None


class BundleCreate(ProductCreate):
    """Create a bundle product with its component items."""

    product_type: ProductType = ProductType.BUNDLE
    bundle_items: list[BundleItemCreate] = Field(..., min_length=1)


class BundleUpdate(ProductUpdate):
    """Update a bundle product. Bundle items managed separately."""

    pass


class BundleDetailResponse(ProductDetail):
    """Bundle product detail with component items and savings info."""

    bundle_items: list[BundleItemResponse] = []
    total_individual_price_ngn: Optional[Decimal] = None
    savings_percent: Optional[Decimal] = None
