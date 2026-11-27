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
    release_wallet_hold,
)
from sqlalchemy import select
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
)
from services.store_service.models.procurement import (
    StoreCostEvent,
    StoreInventoryValuation,
    StoreTradeReceivable,
)
from services.store_service.services.inventory_costing import consumption_cost_minor
from services.store_service.services.ledger_sync import post_order_cogs_journal
from services.store_service.services.order_lifecycle import restore_order_store_credits
from services.store_service.services.order_policy import sync_order_status_dimensions
from services.store_service.services.preorder_allocation import (
    allocate_stock_to_paid_preorders,
    explicit_order_reserved_qty,
    is_dropship_line,
    is_preorder_line,
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
    """Release reserved inventory for a cancelled/failed order and reallocate FIFO."""
    items_query = (
        select(OrderItem)
        .where(OrderItem.order_id == order.id)
        .options(
            selectinload(OrderItem.variant).selectinload(ProductVariant.product),
            selectinload(OrderItem.variant).selectinload(ProductVariant.inventory_item),
        )
    )
    items_result = await db.execute(items_query)
    order_items = items_result.scalars().all()

    for item in order_items:
        if is_dropship_line(item):
            continue
        inv = await db.scalar(
            select(InventoryItem)
            .where(InventoryItem.variant_id == item.variant_id)
            .with_for_update()
        )
        if not inv:
            continue
        if is_preorder_line(item):
            explicit_reserved = await explicit_order_reserved_qty(db, inv.id, order.id)
            release_qty = min(item.quantity, explicit_reserved, inv.quantity_reserved)
        else:
            release_qty = min(item.quantity, inv.quantity_reserved)
        if release_qty <= 0:
            continue

        inv.quantity_reserved -= release_qty

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

        await allocate_stock_to_paid_preorders(
            db,
            inv,
            performed_by,
            reason=f"reallocated after #{order.order_number} {order.status.value}",
            exclude_order_id=order.id,
        )

    logger.info(
        "Released inventory for order %s (%d items)",
        order.order_number,
        len(order_items),
    )


async def _consume_fulfilled_order_stock(
    db: AsyncSession,
    order: Order,
    performed_by: str,
) -> int:
    """Decrement physical stock ONCE when a verified order is handed over.

    Industry-standard sourcing rules (ADR-024, Option A strict):
    - ``dropship``: supplier ships directly; skips warehouse inventory and always
      emits an explicit ``sale_unvalued`` cost event so Finance reconciles supplier cost.
    - ``stocked``: requires full checkout reservation (``quantity_reserved >= qty``
      and ``quantity_on_hand >= qty``) and deducts both.
    - ``preorder``: consumes its explicit/FIFO order reservation plus any needed
      unreserved ``quantity_on_hand`` (never touching another order's reservation).
      Strictly rejects handover with ``409`` when insufficient stock has been
      received into Inventory/Procurement so stock & COGS are never untracked.
    - Valued SKUs consume moving weighted-average cost and return total COGS
      (in kobo) to post to Ledger. Unvalued SKUs record explicit
      ``sale_unvalued`` cost events per ADR-023 rather than fabricating ₦0 COGS.
    """
    lines = (
        await db.scalars(
            select(OrderItem)
            .where(OrderItem.order_id == order.id)
            .options(
                selectinload(OrderItem.variant).selectinload(ProductVariant.product)
            )
        )
    ).all()
    total_cogs_minor = 0
    for line in lines:
        if is_dropship_line(line):
            db.add(
                StoreCostEvent(
                    variant_id=line.variant_id,
                    event_kind="sale_unvalued",
                    quantity_delta=-line.quantity,
                    cost_delta_minor=0,
                    reference=f"sale-unvalued:{line.id}",
                    evidence=f"dropship-fulfilled-order:{order.id}",
                    actor_auth_id=performed_by,
                )
            )
            continue

        inventory = await db.scalar(
            select(InventoryItem)
            .where(
                InventoryItem.variant_id == line.variant_id,
            )
            .with_for_update()
        )
        reserved_to_consume = line.quantity
        movement_note = "Fulfilled order"

        if not is_preorder_line(line):
            if inventory is None:
                raise HTTPException(
                    409, "Fulfillment inventory is missing; reconcile stock first"
                )
            if (
                inventory.quantity_reserved < line.quantity
                or inventory.quantity_on_hand < line.quantity
            ):
                raise HTTPException(
                    409,
                    "Order stock reservation is insufficient; reconcile before fulfillment",
                )
        else:
            explicit_reserved = (
                await explicit_order_reserved_qty(db, inventory.id, order.id)
                if inventory is not None
                else 0
            )
            reserved_portion = (
                min(explicit_reserved, inventory.quantity_reserved, line.quantity)
                if inventory is not None
                else 0
            )
            unreserved_needed = line.quantity - reserved_portion
            available = (
                max(0, inventory.quantity_on_hand - inventory.quantity_reserved)
                if inventory is not None
                else 0
            )
            if (
                inventory is None
                or inventory.quantity_on_hand < line.quantity
                or available < unreserved_needed
            ):
                label = (
                    f"{line.product_name} ({line.variant_name})"
                    if getattr(line, "variant_name", None)
                    else getattr(line, "product_name", "item")
                )
                raise HTTPException(
                    409,
                    f"Cannot fulfill preorder item {label}: insufficient stock received on hand "
                    f"(need {line.quantity}, have {reserved_portion} reserved for this order and "
                    f"{available} unreserved). Record a Purchase Order Goods Receipt or Inventory "
                    "Restock first so stock and COGS are tracked.",
                )
            reserved_to_consume = reserved_portion
            if reserved_portion < line.quantity:
                movement_note = (
                    "Fulfilled preorder order (consumed unreserved on-hand stock)"
                )

        value = await db.scalar(
            select(StoreInventoryValuation)
            .where(
                StoreInventoryValuation.variant_id == line.variant_id,
            )
            .with_for_update()
        )
        if (
            value is not None
            and value.quantity == inventory.quantity_on_hand
            and value.quantity > 0
        ):
            consumed_cost = consumption_cost_minor(
                value.total_cost_minor,
                value.quantity,
                line.quantity,
            )
            value.quantity -= line.quantity
            value.total_cost_minor -= consumed_cost
            total_cogs_minor += consumed_cost
            db.add(
                StoreCostEvent(
                    variant_id=line.variant_id,
                    event_kind="sale",
                    quantity_delta=-line.quantity,
                    cost_delta_minor=-consumed_cost,
                    reference=f"sale:{line.id}",
                    evidence=f"fulfilled-order:{order.id}",
                    actor_auth_id=performed_by,
                )
            )
        else:
            db.add(
                StoreCostEvent(
                    variant_id=line.variant_id,
                    event_kind="sale_unvalued",
                    quantity_delta=-line.quantity,
                    cost_delta_minor=0,
                    reference=f"sale-unvalued:{line.id}",
                    evidence=f"fulfilled-order:{order.id}",
                    actor_auth_id=performed_by,
                )
            )
        inventory.quantity_on_hand -= line.quantity
        if reserved_to_consume > 0:
            inventory.quantity_reserved -= reserved_to_consume
        db.add(
            InventoryMovement(
                inventory_item_id=inventory.id,
                movement_type=InventoryMovementType.SALE,
                quantity=-line.quantity,
                reference_type="order",
                reference_id=order.id,
                notes=movement_note,
                performed_by=performed_by,
            )
        )
    return total_cogs_minor


async def _apply_order_status_change(
    db: AsyncSession,
    order: Order,
    new_status: OrderStatus,
    admin_notes: Optional[str],
    current_user: AuthUser,
) -> None:
    """Apply a status change to an already-loaded order, including all side effects."""
    old_status = order.status
    # Financial settlement is owned by payments_service (or verified credit settlement);
    # the admin's operational status dropdown must never become an unaudited payment proof.
    if new_status == OrderStatus.PAID and old_status != OrderStatus.PAID:
        raise HTTPException(
            status_code=409,
            detail="Verify payment through the payment review workflow before marking an order paid",
        )
    unpaid_statuses = {OrderStatus.PENDING_PAYMENT, OrderStatus.PAYMENT_FAILED}
    fulfillment_statuses = {
        OrderStatus.PROCESSING,
        OrderStatus.READY_FOR_PICKUP,
        OrderStatus.PICKED_UP,
        OrderStatus.SHIPPED,
        OrderStatus.DELIVERED,
    }
    if old_status in unpaid_statuses and new_status in fulfillment_statuses:
        approved_credit = None
        if hasattr(db, "scalar") and getattr(order, "id", None) is not None:
            approved_credit = await db.scalar(
                select(StoreTradeReceivable.id).where(
                    StoreTradeReceivable.order_id == order.id,
                    StoreTradeReceivable.status.in_(("approved", "settled")),
                )
            )
        if approved_credit is None:
            raise HTTPException(
                status_code=409,
                detail="Order must have a verified payment or independently approved credit before fulfillment",
            )
    # Paid orders must NOT be silently cancelled through the raw status dropdown
    # without creating a refund obligation (ADR-024).
    paid_statuses = {
        OrderStatus.PAID,
        OrderStatus.PROCESSING,
        OrderStatus.READY_FOR_PICKUP,
        OrderStatus.PICKED_UP,
        OrderStatus.SHIPPED,
        OrderStatus.DELIVERED,
    }
    if old_status in paid_statuses and new_status == OrderStatus.CANCELLED:
        raise HTTPException(
            status_code=409,
            detail=(
                "Paid orders cannot be cancelled through the raw status selector. "
                "Use 'Cancel & Issue Refund' or approve a cancellation case so funds and tenders are refunded."
            ),
        )
    # Physical departure is separate from payment verification. Consume once,
    # not on PAID or READY status. The order row is locked by the caller.
    fulfilled_cogs_minor = 0
    if new_status in {
        OrderStatus.PICKED_UP,
        OrderStatus.DELIVERED,
    } and old_status not in {OrderStatus.PICKED_UP, OrderStatus.DELIVERED}:
        fulfilled_cogs_minor = await _consume_fulfilled_order_stock(
            db, order, current_user.user_id
        )
    sync_order_status_dimensions(order, legacy_status=new_status)

    if admin_notes:
        order.admin_notes = admin_notes

    # Set timestamps based on status
    now = datetime.utcnow()
    if new_status == OrderStatus.SHIPPED and not getattr(order, "shipped_at", None):
        order.shipped_at = now
    if new_status == OrderStatus.DELIVERED and not getattr(order, "delivered_at", None):
        order.delivered_at = now
    if new_status in [OrderStatus.PICKED_UP, OrderStatus.DELIVERED]:
        order.fulfilled_at = now
    elif new_status in (OrderStatus.CANCELLED, OrderStatus.PAYMENT_FAILED):
        if new_status == OrderStatus.CANCELLED:
            order.cancelled_at = now

        # Release inventory reservations for each order item
        await _release_order_inventory(db, order, current_user.user_id)

        # Restore any applied Store Credit idempotently (fixes legacy silent credit loss)
        if getattr(order, "store_credit_applied_ngn", 0):
            await restore_order_store_credits(
                db,
                order,
                current_user.user_id,
                reason=f"Restored for {new_status.value} order {order.order_number}",
            )

        # A pending hold is released. Only captured Bubbles are refunded.
        if order.wallet_hold_id and not order.wallet_transaction_id:
            try:
                await release_wallet_hold(order.wallet_hold_id, calling_service="store")
            except Exception:
                logger.exception(
                    "Failed to release wallet hold for order %s", order.order_number
                )
        elif order.bubbles_applied and order.wallet_transaction_id:
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

    if fulfilled_cogs_minor > 0:
        await post_order_cogs_journal(
            order_id=order.id,
            order_number=order.order_number,
            entry_date=(order.fulfilled_at or datetime.utcnow()).date(),
            total_cogs_minor=fulfilled_cogs_minor,
        )

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
