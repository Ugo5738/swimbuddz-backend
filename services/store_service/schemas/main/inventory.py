"""Inventory schemas (variant inventory items, adjustments, low-stock alerts)."""

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class InventoryVariantProduct(BaseModel):
    """Minimal product info nested in inventory variant."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str


class InventoryVariantInfo(BaseModel):
    """Variant info included in inventory responses."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    sku: str
    name: Optional[str] = None
    product: Optional[InventoryVariantProduct] = None


class InventoryItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    variant_id: uuid.UUID
    quantity_on_hand: int
    quantity_reserved: int
    quantity_available: int
    low_stock_threshold: int
    last_restock_at: Optional[datetime]
    last_sold_at: Optional[datetime]
    variant: Optional[InventoryVariantInfo] = None


class InventoryAdjustment(BaseModel):
    """Adjust inventory (restock, correction, etc.)."""

    quantity: int = Field(..., description="Positive to add, negative to subtract")
    notes: Optional[str] = None


class LowStockItem(BaseModel):
    """Low stock alert item."""

    variant_id: uuid.UUID
    sku: str
    product_name: str
    variant_name: Optional[str]
    quantity_on_hand: int
    quantity_available: int
    low_stock_threshold: int
