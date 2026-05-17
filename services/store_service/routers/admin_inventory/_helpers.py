"""Helpers for admin inventory + orders routers."""

"""Admin store inventory and orders router."""

import uuid
from datetime import datetime
from typing import Optional

from fastapi import HTTPException
from libs.auth.models import AuthUser
from libs.common.logging import get_logger
from libs.common.service_client import (
    credit_member_wallet,
    dispatch_notification,
    emit_rewards_event,
    get_member_by_auth_id,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from services.store_service.models import (
    AuditEntityType,
    InventoryMovement,
    InventoryMovementType,
    Order,
    OrderItem,
    OrderStatus,
    Product,
    ProductVariant,
)
from services.store_service.routers._helpers import log_audit

logger = get_logger(__name__)


def _order_eager_load_options():
    """Eager-load relationships needed to fully render an order response."""
    return (
        selectinload(Order.items)
        .selectinload(OrderItem.variant)
        .selectinload(ProductVariant.product)
        .selectinload(Product.images),
        selectinload(Order.pickup_location),
    )


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


async def _apply_order_status_change(
    db: AsyncSession,
    order: Order,
    new_status: OrderStatus,
    admin_notes: Optional[str],
    current_user: AuthUser,
) -> None:
    """Apply a status change to an already-loaded order, including all side effects."""
    old_status = order.status
    order.status = new_status

    if admin_notes:
        order.admin_notes = admin_notes

    # Set timestamps based on status
    if new_status in [OrderStatus.PICKED_UP, OrderStatus.DELIVERED]:
        order.fulfilled_at = datetime.utcnow()
    elif new_status in (OrderStatus.CANCELLED, OrderStatus.PAYMENT_FAILED):
        if new_status == OrderStatus.CANCELLED:
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
                    description=f"Refund for {new_status.value} order {order.order_number}",
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
        new_value={"status": new_status.value},
        notes=admin_notes,
    )

    await db.commit()
    await db.refresh(order)

    # Send notification to customer for ready/shipped status changes via centralized email
    if new_status in [OrderStatus.READY_FOR_PICKUP, OrderStatus.SHIPPED]:
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

    # Dispatch in-app notifications for customer-facing status changes
    _STATUS_NOTIFICATION_MAP = {
        OrderStatus.READY_FOR_PICKUP: (
            "order_ready_pickup",
            "Ready for Pickup",
            f"Your order #{order.order_number} is ready for pickup!",
            "package",
        ),
        OrderStatus.SHIPPED: (
            "order_shipped",
            "Order Shipped",
            f"Your order #{order.order_number} has been shipped.",
            "truck",
        ),
        OrderStatus.DELIVERED: (
            "order_delivered",
            "Order Delivered",
            f"Your order #{order.order_number} has been delivered.",
            "check-circle",
        ),
        OrderStatus.PICKED_UP: (
            "order_picked_up",
            "Order Picked Up",
            f"Your order #{order.order_number} has been picked up. Enjoy!",
            "check-circle",
        ),
        OrderStatus.CANCELLED: (
            "order_cancelled",
            "Order Cancelled",
            f"Your order #{order.order_number} has been cancelled.",
            "x-circle",
        ),
    }
    notif_config = _STATUS_NOTIFICATION_MAP.get(new_status)
    if notif_config and order.member_auth_id:
        notif_type, notif_title, notif_body, notif_icon = notif_config
        member = await get_member_by_auth_id(
            order.member_auth_id, calling_service="store"
        )
        if member:
            await dispatch_notification(
                type=notif_type,
                category="store",
                member_ids=[str(member["id"])],
                title=notif_title,
                body=notif_body,
                action_url=f"/account/orders/{order.order_number}",
                icon=notif_icon,
                metadata={
                    "order_id": str(order.id),
                    "order_number": order.order_number,
                },
                calling_service="store",
            )

    # Emit events based on status transitions
    if order.member_auth_id:
        if new_status == OrderStatus.SHIPPED:
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
        elif new_status in (OrderStatus.PICKED_UP, OrderStatus.DELIVERED):
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
        elif new_status == OrderStatus.CANCELLED:
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


async def _load_admin_order(db: AsyncSession, order_id: uuid.UUID) -> Order:
    """Load an order by ID with all relationships needed for OrderResponse."""
    query = (
        select(Order).where(Order.id == order_id).options(*_order_eager_load_options())
    )
    result = await db.execute(query)
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order
