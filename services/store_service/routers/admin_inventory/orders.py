"""Admin order lifecycle — list, status, refunds, mark-paid."""

"""Admin store inventory and orders router."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
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

from ._helpers import _apply_order_status_change, _load_admin_order, _order_eager_load_options

router = APIRouter(tags=["admin-store"])

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
        query.options(*_order_eager_load_options())
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


@router.get("/orders/new-count")
async def get_new_order_count(
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Count orders with status PAID that haven't been processed yet."""
    query = select(func.count(Order.id)).where(Order.status == OrderStatus.PAID)
    result = await db.execute(query)
    return {"new_count": result.scalar_one() or 0}


@router.get("/orders/{order_id}", response_model=OrderResponse)
async def get_order_admin(
    order_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Get order detail (admin)."""
    query = (
        select(Order).where(Order.id == order_id).options(*_order_eager_load_options())
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
    order = await _load_admin_order(db, order_id)
    await _apply_order_status_change(
        db, order, status_update.status, status_update.admin_notes, current_user
    )
    return order


@router.patch("/orders/{order_id}", response_model=OrderResponse)
async def update_order(
    order_id: uuid.UUID,
    update: OrderUpdate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Partial update of an order: status, admin notes, and/or tracking number."""
    order = await _load_admin_order(db, order_id)

    if update.status is not None:
        # Status change runs full side-effect pipeline (notifications, audit, refunds, etc).
        # admin_notes from the same payload is folded in by the helper.
        await _apply_order_status_change(
            db, order, update.status, update.admin_notes, current_user
        )

    field_changes = False
    if update.status is None and update.admin_notes is not None:
        order.admin_notes = update.admin_notes
        field_changes = True
    if update.tracking_number is not None:
        # Tracking number is stored in delivery_notes (legacy field name).
        order.delivery_notes = update.tracking_number
        field_changes = True

    if field_changes:
        await db.commit()
        await db.refresh(order)

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
        select(Order).where(Order.id == order_id).options(*_order_eager_load_options())
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
                "name": f"{item.product_name}"
                + (
                    f" - {item.variant_name}"
                    if item.variant_name and item.variant_name != "Default"
                    else ""
                ),
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

        bubbles = order.bubbles_applied or 0
        bubbles_ngn = float(bubbles * 100) if bubbles else 0

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
                "bubbles_applied": bubbles if bubbles else None,
                "bubbles_amount_ngn": bubbles_ngn if bubbles else None,
            },
        )
    except Exception as e:
        # Log but don't fail the order
        logger.error(f"Failed to send order confirmation email: {e}")

    # Notify admins of the new order
    from services.store_service.routers.checkout import _notify_admins_new_order

    await _notify_admins_new_order(order, db)

    # Dispatch in-app notification to buyer
    if order.member_auth_id:
        member = await get_member_by_auth_id(
            order.member_auth_id, calling_service="store"
        )
        if member:
            await dispatch_notification(
                type="order_confirmed",
                category="store",
                member_ids=[str(member["id"])],
                title="Order Confirmed",
                body=f"Your order #{order.order_number} has been confirmed and is being processed.",
                action_url=f"/account/orders/{order.order_number}",
                icon="shopping-bag",
                metadata={
                    "order_id": str(order.id),
                    "order_number": order.order_number,
                    "amount": float(order.total_ngn),
                },
                calling_service="store",
            )

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
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Cannot issue credit to guest order",
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
