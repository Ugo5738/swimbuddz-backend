"""Store checkout router: Paystack payment initialization and verification."""

from fastapi import APIRouter, Depends, HTTPException
from libs.auth.dependencies import get_current_user
from libs.auth.models import AuthUser
from libs.common.logging import get_logger
from libs.common.service_client import (
    emit_rewards_event,
    initialize_store_payment,
    verify_store_payment,
)
from libs.db.session import get_async_db
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from services.store_service.models import Order, OrderItem, OrderStatus
from services.store_service.schemas import PaymentInitRequest, PaymentInitResponse

# Paystack redirects back here after payment — the verify page reads ?reference=…
_STORE_PAYMENT_CALLBACK = "/store/checkout/verify"

router = APIRouter(tags=["store"])
logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _send_order_confirmation_email(order: Order, db) -> None:
    """Best-effort: send order confirmation email to the customer.

    Uses the same email template as the webhook path (mark_order_paid).
    Failures are logged but never block the response.
    """
    try:
        from libs.common.emails.client import get_email_client

        # Ensure items are loaded
        if not order.items:
            result = await db.execute(
                select(OrderItem).where(OrderItem.order_id == order.id)
            )
            items_objs = result.scalars().all()
        else:
            items_objs = order.items

        items = [
            {
                "name": f"{item.product_name}"
                + (f" - {item.variant_name}" if item.variant_name else ""),
                "quantity": item.quantity,
                "price": float(item.line_total_ngn),
            }
            for item in items_objs
        ]

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
                "pickup_location": None,
                "delivery_address": None,
                "bubbles_applied": bubbles if bubbles else None,
                "bubbles_amount_ngn": bubbles_ngn if bubbles else None,
            },
        )
    except Exception as e:
        logger.error("Failed to send order confirmation email: %s", e)


# ============================================================================
# PAYMENT INITIALIZATION
# ============================================================================


@router.post("/checkout/payment", response_model=PaymentInitResponse)
async def initialize_payment(
    request: PaymentInitRequest,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Initialize Paystack payment for a pending order.

    Called after ``start_checkout`` when the order requires Paystack payment
    (i.e. ``requires_payment=True`` in the checkout response).
    """
    query = select(Order).where(
        Order.id == request.order_id,
        Order.member_auth_id == current_user.user_id,
    )
    result = await db.execute(query)
    order = result.scalar_one_or_none()

    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if order.status != OrderStatus.PENDING_PAYMENT:
        raise HTTPException(
            status_code=400,
            detail=f"Order is not pending payment (status: {order.status.value})",
        )

    if order.total_ngn <= 0:
        raise HTTPException(
            status_code=400,
            detail="Order total is zero — no payment required",
        )

    # Check if already initialized (idempotent — return existing reference)
    if order.payment_reference:
        try:
            verification = await verify_store_payment(
                order.payment_reference, calling_service="store"
            )
            if verification.get("status") == "pending":
                # Re-initialize Paystack (reference exists but user may need a new URL)
                payment_data = await initialize_store_payment(
                    str(order.id),
                    amount_ngn=float(order.total_ngn),
                    member_auth_id=current_user.user_id,
                    member_email=order.customer_email,
                    order_number=order.order_number,
                    callback_url=_STORE_PAYMENT_CALLBACK,
                    calling_service="store",
                )
                return PaymentInitResponse(
                    payment_reference=payment_data["reference"],
                    authorization_url=payment_data["authorization_url"],
                    access_code=payment_data["access_code"],
                )
        except Exception:
            logger.warning(
                "Failed to verify existing reference %s, re-initializing",
                order.payment_reference,
            )

    # Initialize Paystack payment via payments_service
    try:
        payment_data = await initialize_store_payment(
            str(order.id),
            amount_ngn=float(order.total_ngn),
            member_auth_id=current_user.user_id,
            member_email=order.customer_email,
            order_number=order.order_number,
            callback_url=_STORE_PAYMENT_CALLBACK,
            calling_service="store",
        )
    except Exception as e:
        logger.error(
            "Failed to initialize Paystack for order %s: %s", order.order_number, e
        )
        raise HTTPException(
            status_code=502,
            detail="Could not initialize payment. Please try again.",
        )

    # Store the reference on the order for reconciliation
    order.payment_reference = payment_data["reference"]
    await db.commit()

    return PaymentInitResponse(
        payment_reference=payment_data["reference"],
        authorization_url=payment_data["authorization_url"],
        access_code=payment_data["access_code"],
    )


# ============================================================================
# PAYMENT VERIFICATION
# ============================================================================


@router.get("/checkout/verify/{reference}")
async def verify_payment(
    reference: str,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Verify a Paystack payment by reference.

    This is a client-side verification endpoint for when the user returns from
    Paystack checkout. The authoritative payment confirmation is the webhook;
    this endpoint lets the frontend poll status.
    """
    # Find order by payment reference
    query = (
        select(Order)
        .where(
            Order.payment_reference == reference,
            Order.member_auth_id == current_user.user_id,
        )
        .options(selectinload(Order.items))
    )
    result = await db.execute(query)
    order = result.scalar_one_or_none()

    if not order:
        raise HTTPException(
            status_code=404, detail="Order not found for this reference"
        )

    def _verify_response(status: str, message: str) -> dict:
        """Build verify response with full price breakdown."""
        bubbles = order.bubbles_applied or 0
        bubbles_ngn = float(bubbles * 100) if bubbles else 0
        return {
            "status": status,
            "order_number": order.order_number,
            "order_id": str(order.id),
            "amount_ngn": float(order.total_ngn),
            "subtotal_ngn": float(order.subtotal_ngn),
            "discount_ngn": float(order.discount_amount_ngn),
            "delivery_fee_ngn": float(order.delivery_fee_ngn),
            "bubbles_applied": bubbles if bubbles else None,
            "bubbles_amount_ngn": bubbles_ngn if bubbles else None,
            "message": message,
        }

    # If already paid (webhook beat us), return success
    if order.status == OrderStatus.PAID:
        return _verify_response("success", "Payment confirmed")

    # Verify with payments_service
    try:
        verification = await verify_store_payment(reference, calling_service="store")
    except Exception as e:
        logger.error("Failed to verify payment %s: %s", reference, e)
        return _verify_response(
            "pending", "Payment verification in progress. Please wait."
        )

    payment_status = verification.get("status", "unknown")

    if payment_status == "completed" and order.status == OrderStatus.PENDING_PAYMENT:
        # Mark as paid (webhook may also do this — idempotent in mark_order_paid)
        order.status = OrderStatus.PAID
        from datetime import datetime

        order.paid_at = datetime.utcnow()
        await db.commit()

        # Emit purchase event
        await emit_rewards_event(
            event_type="store.purchase_completed",
            member_auth_id=current_user.user_id,
            service_source="store",
            event_data={
                "order_number": order.order_number,
                "total_ngn": float(order.total_ngn),
                "items_count": len(order.items),
            },
            idempotency_key=f"store-purchase-{order.id}",
            calling_service="store",
        )

        # NOTE: Confirmation email is sent by the Paystack webhook
        # (mark_order_paid in admin_inventory.py) to avoid duplicate emails
        # when both verify and webhook fire for the same order.

        return _verify_response("success", "Payment confirmed")
    elif payment_status == "failed":
        order.status = OrderStatus.PAYMENT_FAILED
        await db.commit()
        return _verify_response("failed", "Payment failed. Please try again.")
    else:
        return _verify_response("pending", "Payment is being processed. Please wait.")
