"""Admin inventory management — list, low-stock, adjust."""

"""Admin store inventory and orders router."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.common.logging import get_logger
from libs.common.service_client import (
    credit_member_wallet,
    dispatch_notification,
    emit_rewards_event,
    get_member_by_auth_id,
)
from libs.db.session import get_async_db
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from services.store_service.models import (
    AuditEntityType,
    InventoryItem,
    InventoryMovement,
    InventoryMovementType,
    Order,
    OrderItem,
    OrderStatus,
    Product,
    ProductVariant,
    StoreCredit,
    StoreCreditSourceType,
)
from services.store_service.routers._helpers import log_audit
from services.store_service.schemas import (
    InventoryAdjustment,
    InventoryItemResponse,
    LowStockItem,
    OrderListResponse,
    OrderResponse,
    OrderStatusUpdate,
    OrderUpdate,
    StoreCreditResponse,
)

logger = get_logger(__name__)

router = APIRouter(tags=["admin-store"])

@router.get("/inventory", response_model=list[InventoryItemResponse])
async def list_inventory(
    low_stock_only: bool = False,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """List inventory items."""
    query = select(InventoryItem).options(
        selectinload(InventoryItem.variant).selectinload(ProductVariant.product)
    )

    if low_stock_only:
        query = query.where(
            InventoryItem.quantity_on_hand <= InventoryItem.low_stock_threshold
        )

    result = await db.execute(query)
    items = result.scalars().all()

    return [InventoryItemResponse.model_validate(item) for item in items]


@router.get("/inventory/low-stock", response_model=list[LowStockItem])
async def get_low_stock_items(
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Get items below low stock threshold."""
    query = (
        select(InventoryItem)
        .where(InventoryItem.quantity_on_hand <= InventoryItem.low_stock_threshold)
        .options(
            selectinload(InventoryItem.variant).selectinload(ProductVariant.product)
        )
    )
    result = await db.execute(query)
    items = result.scalars().all()

    return [
        LowStockItem(
            variant_id=item.variant_id,
            sku=item.variant.sku,
            product_name=(
                item.variant.product.name if item.variant.product else "Unknown"
            ),
            variant_name=item.variant.name,
            quantity_on_hand=item.quantity_on_hand,
            quantity_available=item.quantity_available,
            low_stock_threshold=item.low_stock_threshold,
        )
        for item in items
    ]


@router.patch("/inventory/{inventory_id}", response_model=InventoryItemResponse)
async def adjust_inventory(
    inventory_id: uuid.UUID,
    adjustment: InventoryAdjustment,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Adjust inventory (restock or correction)."""
    query = select(InventoryItem).where(InventoryItem.id == inventory_id)
    result = await db.execute(query)
    item = result.scalar_one_or_none()

    if not item:
        raise HTTPException(status_code=404, detail="Inventory item not found")

    old_quantity = item.quantity_on_hand
    new_quantity = old_quantity + adjustment.quantity

    if new_quantity < 0:
        raise HTTPException(status_code=400, detail="Cannot reduce inventory below 0")

    if new_quantity < item.quantity_reserved:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot reduce below reserved quantity ({item.quantity_reserved})",
        )

    item.quantity_on_hand = new_quantity

    # Determine movement type
    if adjustment.quantity > 0:
        movement_type = InventoryMovementType.RESTOCK
        item.last_restock_at = datetime.utcnow()
    else:
        movement_type = InventoryMovementType.ADJUSTMENT

    # Log movement
    movement = InventoryMovement(
        inventory_item_id=item.id,
        movement_type=movement_type,
        quantity=adjustment.quantity,
        reference_type="manual",
        notes=adjustment.notes,
        performed_by=current_user.user_id,
    )
    db.add(movement)

    await log_audit(
        db,
        AuditEntityType.INVENTORY,
        item.id,
        "stock_adjusted",
        current_user.user_id,
        old_value={"quantity_on_hand": old_quantity},
        new_value={"quantity_on_hand": new_quantity},
        notes=adjustment.notes,
    )

    await db.commit()

    # Emit low stock event if stock fell below threshold
    if (
        new_quantity <= item.low_stock_threshold
        and old_quantity > item.low_stock_threshold
    ):
        await emit_rewards_event(
            event_type="store.inventory_low",
            member_auth_id=current_user.user_id,  # Admin who triggered
            service_source="store",
            event_data={
                "variant_id": str(item.variant_id),
                "quantity_on_hand": new_quantity,
                "low_stock_threshold": item.low_stock_threshold,
            },
            idempotency_key=f"store-low-stock-{item.id}-{new_quantity}",
            calling_service="store",
        )

    # Re-fetch with eager loading for nested variant/product response
    detail_query = (
        select(InventoryItem)
        .where(InventoryItem.id == item.id)
        .options(
            selectinload(InventoryItem.variant).selectinload(ProductVariant.product)
        )
    )
    detail_result = await db.execute(detail_query)
    refreshed_item = detail_result.scalar_one()

    return InventoryItemResponse.model_validate(refreshed_item)
