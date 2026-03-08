"""Admin store maintenance: cart expiry, abandoned cart cleanup, stale reservations."""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.common.logging import get_logger
from libs.db.session import get_async_db
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from services.store_service.models import (
    Cart,
    CartItem,
    CartStatus,
    InventoryMovement,
    InventoryMovementType,
    Order,
    OrderStatus,
    ProductVariant,
)

router = APIRouter(tags=["admin-store"])
logger = get_logger(__name__)


class CleanupResult(BaseModel):
    expired_carts: int
    reservations_released: int
    stale_orders_failed: int
    message: str


# ============================================================================
# CART EXPIRY & CLEANUP
# ============================================================================


@router.post("/maintenance/cleanup", response_model=CleanupResult)
async def run_cleanup(
    expire_minutes: int = Query(
        30, ge=5, le=1440, description="Minutes after which carts expire"
    ),
    stale_order_hours: int = Query(
        24,
        ge=1,
        le=168,
        description="Hours after which unpaid orders are marked failed",
    ),
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Run store cleanup: expire stale carts, release inventory, mark stale orders.

    This endpoint can be called periodically via a cron job or manually by admin.

    Actions performed:
    1. Expire active carts older than ``expire_minutes``
    2. Release inventory reservations for expired carts
    3. Mark pending_payment orders older than ``stale_order_hours`` as payment_failed
    """
    now = datetime.utcnow()

    # --- 1. Find and expire stale active carts ---
    cutoff = now - timedelta(minutes=expire_minutes)
    stale_carts_query = (
        select(Cart)
        .where(
            Cart.status == CartStatus.ACTIVE,
            Cart.updated_at < cutoff,
        )
        .options(
            selectinload(Cart.items)
            .selectinload(CartItem.variant)
            .selectinload(ProductVariant.inventory_item)
        )
    )
    result = await db.execute(stale_carts_query)
    stale_carts = list(result.scalars().all())

    expired_count = 0
    reservations_released = 0

    for cart in stale_carts:
        cart.status = CartStatus.EXPIRED

        # Release any inventory reservations held by this cart's items
        for item in cart.items:
            if not item.variant or not item.variant.inventory_item:
                continue
            inv = item.variant.inventory_item
            release_qty = min(item.quantity, inv.quantity_reserved)
            if release_qty <= 0:
                continue

            inv.quantity_reserved -= release_qty
            reservations_released += release_qty

            movement = InventoryMovement(
                inventory_item_id=inv.id,
                movement_type=InventoryMovementType.RELEASE,
                quantity=-release_qty,
                reference_type="cart_expiry",
                reference_id=cart.id,
                notes=f"Cart expired after {expire_minutes}min inactivity",
                performed_by="system",
            )
            db.add(movement)

        expired_count += 1

    # --- 2. Mark stale pending_payment orders as payment_failed ---
    order_cutoff = now - timedelta(hours=stale_order_hours)
    stale_orders_query = select(Order).where(
        Order.status == OrderStatus.PENDING_PAYMENT,
        Order.created_at < order_cutoff,
    )
    stale_result = await db.execute(stale_orders_query)
    stale_orders = list(stale_result.scalars().all())

    stale_order_count = 0
    for order in stale_orders:
        order.status = OrderStatus.PAYMENT_FAILED
        stale_order_count += 1

    await db.commit()

    msg_parts = []
    if expired_count:
        msg_parts.append(f"Expired {expired_count} carts")
    if reservations_released:
        msg_parts.append(f"Released {reservations_released} inventory units")
    if stale_order_count:
        msg_parts.append(f"Failed {stale_order_count} stale orders")
    message = ". ".join(msg_parts) if msg_parts else "No cleanup needed"

    logger.info("Store cleanup: %s", message)

    return CleanupResult(
        expired_carts=expired_count,
        reservations_released=reservations_released,
        stale_orders_failed=stale_order_count,
        message=message,
    )
