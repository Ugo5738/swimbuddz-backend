"""Store orders router: checkout, order history, and store credits."""

from datetime import datetime, timedelta
from decimal import Decimal

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from libs.auth.dependencies import get_current_user
from libs.auth.models import AuthUser
from libs.common.currency import bubbles_to_naira, naira_to_bubbles
from libs.common.service_client import (
    check_wallet_balance,
    debit_member_wallet,
    emit_rewards_event,
    get_member_by_auth_id,
)
from libs.db.session import get_async_db
from services.store_service.models import (
    Cart,
    CartItem,
    CartStatus,
    FulfillmentType,
    InventoryMovement,
    InventoryMovementType,
    Order,
    OrderItem,
    OrderStatus,
    PickupLocation,
    Product,
    ProductVariant,
    SourcingType,
    StoreCredit,
)
from services.store_service.routers.cart import (
    _resolve_coupon_discount,
    calculate_cart_totals,
)
from services.store_service.schemas import (
    CheckoutStartRequest,
    CheckoutStartResponse,
    MemberStoreCreditSummary,
    OrderResponse,
)

router = APIRouter(tags=["store"])

# Constants
DELIVERY_FEE_NGN = Decimal("2000")  # Flat delivery fee for now


# ============================================================================
# CHECKOUT
# ============================================================================


@router.post("/checkout/start", response_model=CheckoutStartResponse)
async def start_checkout(
    request: CheckoutStartRequest,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Start checkout process - validate cart, reserve inventory, create pending order."""
    # Get member's active cart
    query = (
        select(Cart)
        .where(
            Cart.member_auth_id == current_user.user_id,
            Cart.status == CartStatus.ACTIVE,
        )
        .order_by(Cart.updated_at.desc())
        .options(
            selectinload(Cart.items)
            .selectinload(CartItem.variant)
            .selectinload(ProductVariant.product)
            .selectinload(Product.supplier),
            selectinload(Cart.items)
            .selectinload(CartItem.variant)
            .selectinload(ProductVariant.inventory_item),
        )
    )
    result = await db.execute(query)
    cart = result.scalars().first()

    if not cart or not cart.items:
        raise HTTPException(status_code=400, detail="Cart is empty")

    # Validate fulfillment
    if request.fulfillment_type == FulfillmentType.PICKUP:
        if not request.pickup_location_id:
            raise HTTPException(status_code=400, detail="Pickup location required")
        # Validate pickup location exists
        loc_query = select(PickupLocation).where(
            PickupLocation.id == request.pickup_location_id,
            PickupLocation.is_active.is_(True),
        )
        loc_result = await db.execute(loc_query)
        if not loc_result.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="Invalid pickup location")
    elif request.fulfillment_type == FulfillmentType.DELIVERY:
        if not request.delivery_address:
            raise HTTPException(status_code=400, detail="Delivery address required")

    # Check size chart acknowledgment if needed
    needs_size_ack = any(
        item.variant.product.requires_size_chart_ack for item in cart.items
    )
    if needs_size_ack and not request.size_chart_acknowledged:
        raise HTTPException(
            status_code=400,
            detail="Size chart acknowledgment required for swimwear products",
        )

    # Validate inventory and reserve (skip stock checks for pre-order/dropship)
    for item in cart.items:
        inv = item.variant.inventory_item
        product = item.variant.product
        is_preorder = product and product.sourcing_type in (
            SourcingType.PREORDER,
            SourcingType.DROPSHIP,
        )

        if not inv and not is_preorder:
            raise HTTPException(
                status_code=400,
                detail=f"Inventory not available for {item.variant.sku}",
            )
        if inv and not is_preorder and inv.quantity_available < item.quantity:
            raise HTTPException(
                status_code=400,
                detail=f"Only {inv.quantity_available} available for {item.variant.sku}",
            )
        # Reserve inventory (only for stocked items, not pre-order/dropship)
        if inv and not is_preorder:
            inv.quantity_reserved += item.quantity
            # Log movement
            movement = InventoryMovement(
                inventory_item_id=inv.id,
                movement_type=InventoryMovementType.RESERVATION,
                quantity=item.quantity,
                reference_type="cart",
                reference_id=cart.id,
            )
            db.add(movement)

    # Get member info for order via members service HTTP API
    member = await get_member_by_auth_id(current_user.user_id, calling_service="store")
    if not member:
        raise HTTPException(status_code=400, detail="Member profile not found")

    # Calculate totals (including coupon discount if code applied)
    raw_subtotal = sum(item.unit_price_ngn * item.quantity for item in cart.items)
    coupon_discount = await _resolve_coupon_discount(cart, raw_subtotal)
    subtotal, discount_amount, total = await calculate_cart_totals(
        cart, coupon_discount_ngn=coupon_discount
    )

    # Add delivery fee if applicable
    delivery_fee = (
        DELIVERY_FEE_NGN
        if request.fulfillment_type == FulfillmentType.DELIVERY
        else Decimal("0")
    )
    final_total = total + delivery_fee

    # Apply store credits if requested
    store_credit_applied = Decimal("0")
    if request.apply_store_credit:
        # Get available store credits for member
        credits_query = (
            select(StoreCredit)
            .where(
                StoreCredit.member_auth_id == current_user.user_id,
                StoreCredit.balance_ngn > 0,
            )
            .order_by(StoreCredit.created_at)  # FIFO - oldest credits first
        )
        credits_result = await db.execute(credits_query)
        available_credits = list(credits_result.scalars().all())

        remaining_to_cover = final_total
        for credit in available_credits:
            if remaining_to_cover <= 0:
                break
            # Apply from this credit
            apply_amount = min(credit.balance_ngn, remaining_to_cover)
            credit.balance_ngn -= apply_amount
            store_credit_applied += apply_amount
            remaining_to_cover -= apply_amount

    # Amount remaining after store credit
    amount_after_credit = final_total - store_credit_applied

    # Bubbles wallet payment (partial or full)
    bubbles_applied: int | None = None
    bubbles_amount_ngn = Decimal("0")
    wallet_txn_id: str | None = None
    paystack_amount = amount_after_credit  # Amount that must go to Paystack

    if (
        request.bubbles_to_apply
        and request.bubbles_to_apply > 0
        and amount_after_credit > 0
    ):
        bubbles_requested = request.bubbles_to_apply

        # Convert Bubbles to NGN and cap at amount_after_credit
        bubbles_ngn_value = Decimal(str(bubbles_to_naira(bubbles_requested)))
        if bubbles_ngn_value > amount_after_credit:
            # Cap: member asked for more Bubbles than the remaining total
            # Only debit enough to cover the bill
            bubbles_requested = naira_to_bubbles(float(amount_after_credit))
            if bubbles_requested == 0:
                bubbles_requested = 1  # Minimum 1 Bubble if any credit remains
            bubbles_ngn_value = min(
                Decimal(str(bubbles_to_naira(bubbles_requested))),
                amount_after_credit,
            )

        # Pre-flight: check balance (non-destructive)
        balance_check = await check_wallet_balance(
            current_user.user_id,
            required_amount=bubbles_requested,
            calling_service="store",
        )
        if not balance_check or not balance_check.get("sufficient"):
            current_balance = (
                balance_check.get("current_balance", 0) if balance_check else 0
            )
            raise HTTPException(
                status_code=402,
                detail=(
                    f"Insufficient Bubbles. Need {bubbles_requested} 🫧, "
                    f"have {current_balance} 🫧."
                ),
            )

        bubbles_applied = bubbles_requested
        bubbles_amount_ngn = bubbles_ngn_value
        paystack_amount = amount_after_credit - bubbles_amount_ngn

    # Create order (flush to get ID before wallet debit for idempotency key)
    order = Order(
        order_number=Order.generate_order_number(),
        member_auth_id=current_user.user_id,
        customer_email=member["email"],
        customer_name=f"{member['first_name']} {member['last_name']}",
        customer_phone=member.get("phone")
        or (request.delivery_address.phone if request.delivery_address else None),
        subtotal_ngn=subtotal,
        discount_amount_ngn=discount_amount,
        store_credit_applied_ngn=store_credit_applied,
        delivery_fee_ngn=delivery_fee,
        total_ngn=paystack_amount,  # Amount remaining for Paystack (or 0 if fully covered)
        discount_code=cart.discount_code,
        discount_breakdown={
            "member_tier_discount": float(cart.member_discount_percent or 0),
            "coupon_code": cart.discount_code,
        },
        status=OrderStatus.PENDING_PAYMENT,
        fulfillment_type=request.fulfillment_type,
        pickup_location_id=request.pickup_location_id,
        delivery_address=(
            request.delivery_address.model_dump() if request.delivery_address else None
        ),
        customer_notes=request.customer_notes,
    )
    db.add(order)
    await db.flush()  # Get order ID

    # Debit Bubbles after we have the order ID (use it as idempotency scope)
    if bubbles_applied and bubbles_applied > 0:
        try:
            result_txn = await debit_member_wallet(
                current_user.user_id,
                amount=bubbles_applied,
                idempotency_key=f"order-{order.id}",
                description=f"Store order {order.order_number} ({bubbles_applied} 🫧)",
                calling_service="store",
                transaction_type="purchase",
                reference_type="order",
                reference_id=str(order.id),
            )
            wallet_txn_id = result_txn.get("transaction_id")
            order.bubbles_applied = bubbles_applied
            order.wallet_transaction_id = wallet_txn_id

            # If Bubbles covered the entire amount, mark as PAID
            if paystack_amount <= 0:
                order.status = OrderStatus.PAID
                order.paid_at = datetime.utcnow()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 400:
                detail = e.response.json().get("detail", "")
                if "Insufficient" in detail:
                    raise HTTPException(
                        status_code=402,
                        detail="Insufficient Bubbles. Please top up your wallet.",
                    )
            raise HTTPException(status_code=502, detail="Payment service error.")

    # Create order items
    for item in cart.items:
        variant = item.variant
        product = variant.product
        order_item = OrderItem(
            order_id=order.id,
            variant_id=variant.id,
            product_name=product.name,
            variant_name=variant.name,
            sku=variant.sku,
            quantity=item.quantity,
            unit_price_ngn=item.unit_price_ngn,
            line_total_ngn=item.unit_price_ngn * item.quantity,
            is_preorder=product.sourcing_type.value == "preorder",
            estimated_ship_date=(
                datetime.utcnow() + timedelta(days=product.preorder_lead_days or 0)
                if product.sourcing_type.value == "preorder"
                else None
            ),
            # Supplier snapshot (captured at order time)
            supplier_id=product.supplier_id,
            supplier_name=(product.supplier.name if product.supplier else None),
        )
        db.add(order_item)

    # Mark cart as converted
    cart.status = CartStatus.CONVERTED
    await db.commit()

    # Best-effort: check if this is the member's first store purchase
    first_order_check = await db.execute(
        select(Order.id).where(
            Order.member_auth_id == current_user.user_id,
            Order.status.in_([OrderStatus.PAID, OrderStatus.PENDING_PAYMENT]),
            Order.id != order.id,
        )
    )
    if first_order_check.scalar_one_or_none() is None:
        await emit_rewards_event(
            event_type="store.first_purchase",
            member_auth_id=current_user.user_id,
            service_source="store",
            event_data={
                "order_number": order.order_number,
                "total_ngn": float(order.total_ngn),
            },
            idempotency_key=f"store-first-purchase-{current_user.user_id}",
            calling_service="store",
        )

    # If fully paid by Bubbles (no Paystack needed), emit purchase completed event
    # and send order confirmation email
    if order.status == OrderStatus.PAID:
        await emit_rewards_event(
            event_type="store.purchase_completed",
            member_auth_id=current_user.user_id,
            service_source="store",
            event_data={
                "order_number": order.order_number,
                "total_ngn": float(order.total_ngn),
                "payment_method": "bubbles",
                "bubbles_applied": bubbles_applied,
            },
            idempotency_key=f"store-purchase-{order.id}",
            calling_service="store",
        )

        # Send order confirmation email (best-effort)
        from services.store_service.routers.checkout import (
            _notify_admins_new_order,
            _send_order_confirmation_email,
        )

        await _send_order_confirmation_email(order, db)
        await _notify_admins_new_order(order, db)

    return CheckoutStartResponse(
        order_id=order.id,
        order_number=order.order_number,
        total_ngn=order.total_ngn,
        delivery_fee_ngn=order.delivery_fee_ngn,
        requires_payment=(
            order.total_ngn > 0 and order.status == OrderStatus.PENDING_PAYMENT
        ),
        bubbles_applied=bubbles_applied,
        bubbles_amount_ngn=bubbles_amount_ngn if bubbles_applied else None,
        paystack_amount_ngn=paystack_amount if paystack_amount > 0 else None,
    )


# ============================================================================
# ORDERS
# ============================================================================


@router.get("/orders", response_model=list[OrderResponse])
async def list_my_orders(
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """List member's orders."""
    query = (
        select(Order)
        .where(Order.member_auth_id == current_user.user_id)
        .options(
            selectinload(Order.items)
            .selectinload(OrderItem.variant)
            .selectinload(ProductVariant.product)
            .selectinload(Product.images),
            selectinload(Order.pickup_location),
        )
        .order_by(Order.created_at.desc())
    )
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/orders/{order_number}", response_model=OrderResponse)
async def get_order(
    order_number: str,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Get order by order number."""
    query = (
        select(Order)
        .where(
            Order.order_number == order_number,
            Order.member_auth_id == current_user.user_id,
        )
        .options(
            selectinload(Order.items)
            .selectinload(OrderItem.variant)
            .selectinload(ProductVariant.product)
            .selectinload(Product.images),
            selectinload(Order.pickup_location),
        )
    )
    result = await db.execute(query)
    order = result.scalar_one_or_none()

    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


# ============================================================================
# STORE CREDITS
# ============================================================================


@router.get("/credits/me", response_model=MemberStoreCreditSummary)
async def get_my_store_credits(
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Get member's store credits."""
    query = (
        select(StoreCredit)
        .where(
            StoreCredit.member_auth_id == current_user.user_id,
            StoreCredit.balance_ngn > 0,
        )
        .order_by(StoreCredit.created_at.desc())
    )
    result = await db.execute(query)
    credits = result.scalars().all()

    total_balance = sum(c.balance_ngn for c in credits)

    return MemberStoreCreditSummary(
        total_balance_ngn=total_balance,
        credits=credits,
    )
