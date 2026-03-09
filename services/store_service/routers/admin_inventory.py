"""Admin store inventory and orders router."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.common.logging import get_logger
from libs.common.service_client import credit_member_wallet, emit_rewards_event
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
    StoreCreditResponse,
)

router = APIRouter(tags=["admin-store"])
logger = get_logger(__name__)


# ============================================================================
# HELPERS
# ============================================================================


async def _release_order_inventory(
    db: AsyncSession, order: Order, performed_by: str
) -> None:
    """Release reserved inventory for each item in a cancelled/failed order."""
    # Eager-load items with their variant's inventory
    items_query = (
        select(OrderItem)
        .where(OrderItem.order_id == order.id)
        .options(
            selectinload(OrderItem.variant).selectinload(ProductVariant.inventory_item)
        )
    )
    items_result = await db.execute(items_query)
    order_items = items_result.scalars().all()

    for item in order_items:
        variant = item.variant
        if not variant or not variant.inventory_item:
            continue
        inv = variant.inventory_item
        release_qty = min(item.quantity, inv.quantity_reserved)
        if release_qty <= 0:
            continue

        inv.quantity_reserved -= release_qty

        # Log inventory release movement
        movement = InventoryMovement(
            inventory_item_id=inv.id,
            movement_type=InventoryMovementType.RELEASE,
            quantity=-release_qty,
            reference_type="order",
            reference_id=order.id,
            notes=f"Released for {order.status.value} order {order.order_number}",
            performed_by=performed_by,
        )
        db.add(movement)

    logger.info(
        "Released inventory for order %s (%d items)",
        order.order_number,
        len(order_items),
    )


# ============================================================================
# INVENTORY
# ============================================================================


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


# ============================================================================
# ORDERS
# ============================================================================


@router.get("/orders", response_model=OrderListResponse)
async def list_all_orders(
    status_filter: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """List all orders."""
    query = select(Order)

    if status_filter:
        query = query.where(Order.status == status_filter)

    # Count
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Paginate
    query = (
        query.options(selectinload(Order.items), selectinload(Order.pickup_location))
        .order_by(Order.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )

    result = await db.execute(query)
    orders = result.scalars().all()

    return OrderListResponse(
        items=[OrderResponse.model_validate(o) for o in orders],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/orders/{order_id}", response_model=OrderResponse)
async def get_order_admin(
    order_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Get order detail (admin)."""
    query = (
        select(Order)
        .where(Order.id == order_id)
        .options(selectinload(Order.items), selectinload(Order.pickup_location))
    )
    result = await db.execute(query)
    order = result.scalar_one_or_none()

    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


@router.patch("/orders/{order_id}/status", response_model=OrderResponse)
async def update_order_status(
    order_id: uuid.UUID,
    status_update: OrderStatusUpdate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Update order status."""
    query = (
        select(Order)
        .where(Order.id == order_id)
        .options(selectinload(Order.items), selectinload(Order.pickup_location))
    )
    result = await db.execute(query)
    order = result.scalar_one_or_none()

    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    old_status = order.status
    order.status = status_update.status

    if status_update.admin_notes:
        order.admin_notes = status_update.admin_notes

    # Set timestamps based on status
    if status_update.status in [OrderStatus.PICKED_UP, OrderStatus.DELIVERED]:
        order.fulfilled_at = datetime.utcnow()
    elif status_update.status in (OrderStatus.CANCELLED, OrderStatus.PAYMENT_FAILED):
        if status_update.status == OrderStatus.CANCELLED:
            order.cancelled_at = datetime.utcnow()

        # Release inventory reservations for each order item
        await _release_order_inventory(db, order, current_user.user_id)

        # Refund Bubbles if any were applied (covers split-payment Paystack failures too)
        if order.bubbles_applied and order.bubbles_applied > 0:
            try:
                await credit_member_wallet(
                    order.member_auth_id,
                    amount=order.bubbles_applied,
                    idempotency_key=f"refund-order-{order.id}",
                    description=f"Refund for {status_update.status.value} order {order.order_number}",
                    calling_service="store",
                    transaction_type="refund",
                    reference_type="order",
                    reference_id=str(order.id),
                )
                logger.info(
                    f"Refunded {order.bubbles_applied} Bubbles for order {order.order_number}"
                )
            except Exception as e:
                # Log but don't block cancellation on wallet service failure
                logger.error(
                    f"Failed to refund Bubbles for order {order.order_number}: {e}"
                )

    await log_audit(
        db,
        AuditEntityType.ORDER,
        order.id,
        "status_changed",
        current_user.user_id,
        old_value={"status": old_status.value},
        new_value={"status": status_update.status.value},
        notes=status_update.admin_notes,
    )

    await db.commit()
    await db.refresh(order)

    # Send notification to customer for ready/shipped status changes via centralized email
    if status_update.status in [OrderStatus.READY_FOR_PICKUP, OrderStatus.SHIPPED]:
        try:
            from libs.common.emails.client import get_email_client

            pickup_location_str = None
            if order.pickup_location:
                pickup_location_str = f"{order.pickup_location.name}\n{order.pickup_location.address or ''}"

            email_client = get_email_client()
            await email_client.send_template(
                template_type="store_order_ready",
                to_email=order.customer_email,
                template_data={
                    "customer_name": order.customer_name,
                    "order_number": order.order_number,
                    "fulfillment_type": order.fulfillment_type.value,
                    "pickup_location": pickup_location_str,
                    "tracking_number": order.delivery_notes,  # tracking stored in delivery_notes
                },
            )
        except Exception as e:
            logger.error(f"Failed to send order status email: {e}")

    # Emit events based on status transitions
    if order.member_auth_id:
        if status_update.status == OrderStatus.SHIPPED:
            await emit_rewards_event(
                event_type="store.order_shipped",
                member_auth_id=order.member_auth_id,
                service_source="store",
                event_data={
                    "order_number": order.order_number,
                    "fulfillment_type": order.fulfillment_type.value,
                },
                idempotency_key=f"store-order-shipped-{order.id}",
                calling_service="store",
            )
        elif status_update.status in (OrderStatus.PICKED_UP, OrderStatus.DELIVERED):
            await emit_rewards_event(
                event_type="store.order_fulfilled",
                member_auth_id=order.member_auth_id,
                service_source="store",
                event_data={
                    "order_number": order.order_number,
                    "total_ngn": float(order.total_ngn),
                    "fulfillment_type": order.fulfillment_type.value,
                },
                idempotency_key=f"store-order-fulfilled-{order.id}",
                calling_service="store",
            )
        elif status_update.status == OrderStatus.CANCELLED:
            await emit_rewards_event(
                event_type="store.order_cancelled",
                member_auth_id=order.member_auth_id,
                service_source="store",
                event_data={
                    "order_number": order.order_number,
                    "total_ngn": float(order.total_ngn),
                },
                idempotency_key=f"store-order-cancelled-{order.id}",
                calling_service="store",
            )

    return order


@router.post("/orders/{order_id}/mark-paid", response_model=OrderResponse)
async def mark_order_paid(
    order_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Mark an order as paid.
    Called by payments_service when Paystack webhook confirms payment.
    """
    query = (
        select(Order)
        .where(Order.id == order_id)
        .options(selectinload(Order.items), selectinload(Order.pickup_location))
    )
    result = await db.execute(query)
    order = result.scalar_one_or_none()

    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if order.status == OrderStatus.PAID:
        # Already paid, idempotent return
        return order

    old_status = order.status
    order.status = OrderStatus.PAID

    await log_audit(
        db,
        AuditEntityType.ORDER,
        order.id,
        "payment_confirmed",
        current_user.user_id,
        old_value={"status": old_status.value},
        new_value={"status": OrderStatus.PAID.value},
        notes="Payment confirmed via webhook",
    )

    await db.commit()
    await db.refresh(order)

    # Send order confirmation email to customer via centralized email service
    try:
        from libs.common.emails.client import get_email_client

        items = [
            {
                "name": f"{item.product_name} - {item.variant_name or 'Default'}",
                "quantity": item.quantity,
                "price": float(item.line_total_ngn),
            }
            for item in order.items
        ]

        pickup_location_str = None
        if order.pickup_location:
            pickup_location_str = (
                f"{order.pickup_location.name}\n{order.pickup_location.address or ''}"
            )

        delivery_address_str = None
        if order.delivery_address:
            addr = order.delivery_address
            delivery_address_str = f"{addr.get('street', '')}, {addr.get('city', '')}, {addr.get('state', '')}"

        email_client = get_email_client()
        await email_client.send_template(
            template_type="store_order_confirmation",
            to_email=order.customer_email,
            template_data={
                "customer_name": order.customer_name,
                "order_number": order.order_number,
                "items": items,
                "subtotal": float(order.subtotal_ngn),
                "discount": float(order.discount_amount_ngn),
                "delivery_fee": float(order.delivery_fee_ngn),
                "total": float(order.total_ngn),
                "fulfillment_type": order.fulfillment_type.value,
                "pickup_location": pickup_location_str,
                "delivery_address": delivery_address_str,
            },
        )
    except Exception as e:
        # Log but don't fail the order
        logger.error(f"Failed to send order confirmation email: {e}")

    # Emit store.order_paid event for rewards/analytics
    if order.member_auth_id:
        await emit_rewards_event(
            event_type="store.order_paid",
            member_auth_id=order.member_auth_id,
            service_source="store",
            event_data={
                "order_number": order.order_number,
                "total_ngn": float(order.total_ngn),
                "items_count": len(order.items),
                "fulfillment_type": order.fulfillment_type.value,
            },
            idempotency_key=f"store-order-paid-{order.id}",
            calling_service="store",
        )

    return order


@router.post("/orders/{order_id}/refund", response_model=StoreCreditResponse)
async def issue_refund(
    order_id: uuid.UUID,
    amount_ngn: Decimal = Query(..., gt=0),
    reason: Optional[str] = None,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Issue a store credit refund for an order."""
    order = await db.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if not order.member_auth_id:
        raise HTTPException(
            status_code=400, detail="Cannot issue credit to guest order"
        )

    if amount_ngn > order.total_ngn:
        raise HTTPException(
            status_code=400,
            detail=f"Refund amount cannot exceed order total ({order.total_ngn})",
        )

    # Create store credit
    credit = StoreCredit(
        member_auth_id=order.member_auth_id,
        amount_ngn=amount_ngn,
        balance_ngn=amount_ngn,
        source_type=StoreCreditSourceType.RETURN,
        source_order_id=order_id,
        reason=reason,
        issued_by=current_user.user_id,
    )
    db.add(credit)

    # Update order status if full refund
    if amount_ngn >= order.total_ngn:
        order.status = OrderStatus.REFUNDED

    await log_audit(
        db,
        AuditEntityType.STORE_CREDIT,
        credit.id,
        "issued",
        current_user.user_id,
        new_value={"amount_ngn": float(amount_ngn), "order_id": str(order_id)},
        notes=reason,
    )

    await db.commit()
    await db.refresh(credit)

    # TODO: Send notification to customer

    return credit
