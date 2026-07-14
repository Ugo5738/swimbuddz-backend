"""Store checkout router: Paystack payment initialization and verification."""

import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from libs.auth.dependencies import get_current_user
from libs.auth.models import AuthUser
from libs.common.currency import bubbles_to_naira
from libs.common.logging import get_logger
from libs.common.service_client import (
    create_wallet_hold,
    initialize_store_payment,
    release_wallet_hold,
    verify_store_payment,
)
from libs.db.session import get_async_db
from services.store_service.models import Order, OrderItem, OrderStatus
from services.store_service.routers.admin_inventory._helpers import (
    _release_order_inventory,
)
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
        bubbles_ngn = float(bubbles_to_naira(bubbles)) if bubbles else 0

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


async def _notify_admins_new_order(order: Order, db) -> None:
    """Best-effort: send new-order notification email to all admins."""
    try:
        from libs.common.config import get_settings
        from libs.common.emails.client import get_email_client

        settings = get_settings()
        admin_emails = settings.ADMIN_EMAILS or []
        if not admin_emails:
            return

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
                + (
                    f" - {item.variant_name}"
                    if item.variant_name and item.variant_name != "Default"
                    else ""
                ),
                "quantity": item.quantity,
                "price": float(item.line_total_ngn),
            }
            for item in items_objs
        ]

        email_client = get_email_client()
        for admin_email in admin_emails:
            try:
                await email_client.send_template(
                    template_type="store_new_order_admin",
                    to_email=admin_email,
                    template_data={
                        "order_number": order.order_number,
                        "customer_name": order.customer_name,
                        "customer_email": order.customer_email,
                        "items": items,
                        "total": float(order.total_ngn),
                        "fulfillment_type": order.fulfillment_type.value,
                    },
                )
            except Exception as e:
                logger.error(
                    "Failed to send admin notification to %s: %s", admin_email, e
                )
    except Exception as e:
        logger.error("Failed to notify admins of new order: %s", e)


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
        except Exception as exc:
            logger.warning(
                "Could not determine status of existing payment %s",
                order.payment_reference,
                exc_info=True,
            )
            raise HTTPException(
                status_code=503,
                detail=(
                    "The existing payment could not be reconciled yet. "
                    "Please retry shortly; a second charge was not created."
                ),
            ) from exc

        existing_status = verification.get("status")
        if existing_status == "pending":
            try:
                # Re-initialize Paystack (reference exists but user may need a new URL)
                payment_data = await initialize_store_payment(
                    str(order.id),
                    amount_ngn=float(order.total_ngn),
                    member_auth_id=current_user.user_id,
                    member_email=order.customer_email,
                    order_number=order.order_number,
                    callback_url=_STORE_PAYMENT_CALLBACK,
                    reference=order.payment_reference,
                    bubbles_to_apply=order.bubbles_applied or 0,
                    wallet_hold_id=order.wallet_hold_id,
                    calling_service="store",
                )
                return PaymentInitResponse(
                    payment_reference=payment_data["reference"],
                    authorization_url=payment_data["authorization_url"],
                    access_code=payment_data["access_code"],
                )
            except Exception as exc:
                raise HTTPException(
                    status_code=502,
                    detail="Could not resume the existing payment. Please retry shortly.",
                ) from exc
        if existing_status == "completed":
            await db.refresh(order)
            detail = (
                "This order has already been paid."
                if order.status == OrderStatus.PAID
                else "Payment is confirmed and order fulfillment is still processing."
            )
            raise HTTPException(status_code=409, detail=detail)
        if existing_status != "failed":
            raise HTTPException(
                status_code=503,
                detail=(
                    "The existing payment status is not final. "
                    "Please retry shortly; a second charge was not created."
                ),
            )

        # Only a provider-confirmed terminal failure may open a fresh payment.
        if order.wallet_hold_id:
            try:
                await release_wallet_hold(order.wallet_hold_id, calling_service="store")
            except Exception as exc:
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "The failed payment is still being released. "
                        "Please retry shortly."
                    ),
                ) from exc
        order.wallet_hold_id = None
        order.payment_reference = None
        await db.commit()

    payment_reference = f"store-order-{order.id}-{uuid.uuid4().hex[:12]}"
    if order.bubbles_applied and not order.wallet_transaction_id:
        try:
            hold = await create_wallet_hold(
                current_user.user_id,
                amount=order.bubbles_applied,
                idempotency_key=f"{payment_reference}:bubbles",
                description=f"Store order {order.order_number}",
                calling_service="store",
                reference_type="store_order",
                reference_id=str(order.id),
                expires_in_seconds=1800,
            )
        except httpx.HTTPStatusError as exc:
            detail = "Could not reserve the selected Bubbles"
            try:
                detail = exc.response.json().get("detail") or detail
            except ValueError:
                pass
            raise HTTPException(
                status_code=(
                    exc.response.status_code
                    if exc.response.status_code in {400, 402, 404, 409}
                    else 503
                ),
                detail=detail,
            ) from exc
        order.wallet_hold_id = str(hold["id"])

    order.payment_reference = payment_reference
    await db.commit()

    # Initialize Paystack payment via payments_service
    try:
        payment_data = await initialize_store_payment(
            str(order.id),
            amount_ngn=float(order.total_ngn),
            member_auth_id=current_user.user_id,
            member_email=order.customer_email,
            order_number=order.order_number,
            callback_url=_STORE_PAYMENT_CALLBACK,
            reference=payment_reference,
            bubbles_to_apply=order.bubbles_applied or 0,
            wallet_hold_id=order.wallet_hold_id,
            calling_service="store",
        )
    except Exception as e:
        logger.error(
            "Failed to initialize Paystack for order %s: %s", order.order_number, e
        )
        if order.wallet_hold_id:
            try:
                await release_wallet_hold(order.wallet_hold_id, calling_service="store")
            except Exception:
                logger.exception(
                    "Failed to release wallet hold for order %s", order.order_number
                )
        order.wallet_hold_id = None
        order.payment_reference = None
        await db.commit()
        raise HTTPException(
            status_code=502,
            detail="Could not initialize payment. Please try again.",
        )

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
        bubbles_ngn = float(bubbles_to_naira(bubbles)) if bubbles else 0
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

    # Internal verification applies the payment entitlement before returning.
    # Refresh so this request does not repeat mark-paid side effects.
    await db.refresh(order)
    if order.status == OrderStatus.PAID:
        return _verify_response("success", "Payment confirmed")

    if payment_status == "completed":
        return _verify_response(
            "pending", "Payment confirmed. Order fulfillment is still processing."
        )
    if payment_status == "failed":
        order.status = OrderStatus.PAYMENT_FAILED
        await _release_order_inventory(db, order, "system")
        if order.wallet_hold_id:
            try:
                await release_wallet_hold(order.wallet_hold_id, calling_service="store")
            except Exception:
                logger.exception(
                    "Failed to release wallet hold for order %s", order.order_number
                )
        await db.commit()
        return _verify_response("failed", "Payment failed. Please try again.")
    else:
        return _verify_response("pending", "Payment is being processed. Please wait.")
