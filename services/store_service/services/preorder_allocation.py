"""FIFO preorder stock allocation & sourcing helpers for Store inventory (ADR-024).

Separates the three Store sourcing lifecycles cleanly:
- ``stocked``: reserves inventory at checkout; deducts ``on_hand`` + ``reserved`` on handover.
- ``preorder``: allows checkout at 0 stock; when stock later arrives (PO goods receipt,
  admin restock, restockable return, or released reservation), arriving units are
  automatically reserved FIFO for the oldest paid unfulfilled preorder orders first so
  new storefront buyers can never oversell units owed to paid preorder customers.
  Handover strictly requires received stock on hand (Option A).
- ``dropship``: supplier fulfills directly without entering SwimBuddz warehouse inventory;
  never claims warehouse reservations and always records an explicit ``sale_unvalued``
  cost event on handover so Finance reconciles supplier cost.
"""

from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.store_service.models import (
    InventoryItem,
    InventoryMovement,
    InventoryMovementType,
    Order,
    OrderItem,
    OrderStatus,
    SourcingType,
)

PAID_UNFULFILLED_ORDER_STATUSES = (
    OrderStatus.PAID,
    OrderStatus.PROCESSING,
    OrderStatus.READY_FOR_PICKUP,
    OrderStatus.SHIPPED,
)


def is_dropship_line(line: OrderItem) -> bool:
    """Return True if an order line represents supplier-direct dropship fulfillment."""
    sourcing = getattr(line, "sourcing_type", None)
    if sourcing == SourcingType.DROPSHIP.value:
        return True
    variant = getattr(line, "variant", None)
    product = getattr(variant, "product", None) if variant is not None else None
    if (
        sourcing is None
        and not getattr(line, "is_preorder", False)
        and product is not None
        and getattr(product, "sourcing_type", None) == SourcingType.DROPSHIP
    ):
        return True
    return False


def is_preorder_line(line: OrderItem) -> bool:
    """Return True if an order line was purchased as a warehouse-fulfilled preorder."""
    if is_dropship_line(line):
        return False
    return bool(
        getattr(line, "is_preorder", False)
        or getattr(line, "sourcing_type", None) == SourcingType.PREORDER.value
    )


async def explicit_order_reserved_qty(
    db: AsyncSession,
    inventory_item_id: uuid.UUID,
    order_id: uuid.UUID,
) -> int:
    """Return net explicit order-scoped reservation quantity for a variant/order pair."""
    qty = await db.scalar(
        select(func.coalesce(func.sum(InventoryMovement.quantity), 0)).where(
            InventoryMovement.inventory_item_id == inventory_item_id,
            InventoryMovement.reference_type == "order",
            InventoryMovement.reference_id == order_id,
            InventoryMovement.movement_type.in_(
                [InventoryMovementType.RESERVATION, InventoryMovementType.RELEASE]
            ),
        )
    )
    return max(0, int(qty or 0))


async def allocate_stock_to_paid_preorders(
    db: AsyncSession,
    inventory: InventoryItem,
    performed_by: str,
    *,
    reason: Optional[str] = None,
    exclude_order_id: Optional[uuid.UUID] = None,
) -> int:
    """Auto-reserve available stock FIFO for oldest paid unfulfilled preorder orders.

    Caller MUST hold a ``FOR UPDATE`` lock on ``inventory``. Never allocates to
    unpaid orders, cancelled/fulfilled orders, or ``dropship`` lines.
    """
    available = inventory.quantity_on_hand - inventory.quantity_reserved
    if available <= 0:
        return 0

    order_priority = func.coalesce(Order.paid_at, Order.created_at)
    stmt = (
        select(
            Order.id,
            Order.order_number,
            func.sum(OrderItem.quantity).label("needed_qty"),
            order_priority.label("priority_ts"),
            Order.created_at,
        )
        .join(OrderItem, OrderItem.order_id == Order.id)
        .where(
            OrderItem.variant_id == inventory.variant_id,
            Order.status.in_(PAID_UNFULFILLED_ORDER_STATUSES),
            or_(
                OrderItem.is_preorder.is_(True),
                OrderItem.sourcing_type == SourcingType.PREORDER.value,
            ),
            or_(
                OrderItem.sourcing_type.is_(None),
                OrderItem.sourcing_type != SourcingType.DROPSHIP.value,
            ),
        )
        .group_by(Order.id, Order.order_number, order_priority, Order.created_at)
        .order_by(order_priority.asc(), Order.created_at.asc(), Order.id.asc())
    )
    if exclude_order_id is not None:
        stmt = stmt.where(Order.id != exclude_order_id)

    rows = (await db.execute(stmt)).all()
    total_allocated = 0

    for row in rows:
        if available <= 0:
            break
        order_id = row[0]
        order_number = row[1]
        needed_qty = int(row[2] or 0)
        if needed_qty <= 0:
            continue

        already_reserved = await explicit_order_reserved_qty(db, inventory.id, order_id)
        remaining_need = needed_qty - already_reserved
        if remaining_need <= 0:
            continue

        alloc_qty = min(remaining_need, available)
        inventory.quantity_reserved += alloc_qty
        available -= alloc_qty
        total_allocated += alloc_qty

        note_suffix = f" ({reason})" if reason else ""
        db.add(
            InventoryMovement(
                inventory_item_id=inventory.id,
                movement_type=InventoryMovementType.RESERVATION,
                quantity=alloc_qty,
                reference_type="order",
                reference_id=order_id,
                notes=f"Auto-allocated FIFO to paid preorder #{order_number}{note_suffix}",
                performed_by=performed_by,
            )
        )

    return total_allocated


async def allocate_stock_for_newly_paid_preorder_order(
    db: AsyncSession,
    order: Order,
    performed_by: str,
) -> int:
    """When a preorder transitions to PAID, allocate any already-available stock FIFO."""
    items = getattr(order, "items", None) or []
    preorder_variant_ids = sorted(
        {
            item.variant_id
            for item in items
            if is_preorder_line(item) and item.variant_id
        },
        key=str,
    )
    if not preorder_variant_ids:
        return 0

    total_allocated = 0
    for variant_id in preorder_variant_ids:
        inventory = await db.scalar(
            select(InventoryItem)
            .where(InventoryItem.variant_id == variant_id)
            .with_for_update()
        )
        if inventory is None:
            continue
        total_allocated += await allocate_stock_to_paid_preorders(
            db,
            inventory,
            performed_by,
            reason=f"payment confirmed for #{order.order_number}",
        )
    return total_allocated
