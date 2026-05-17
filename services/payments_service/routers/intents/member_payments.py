"""Member-facing payment lookups.

* GET /payments/me — list the authenticated member's payments.
* POST /payments/paystack/verify/{reference} — server-driven Paystack
  verification as a webhook fallback. Idempotent on already-paid
  payments; blocks cross-user access (returns 404).
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import get_current_user
from libs.auth.models import AuthUser
from libs.common.config import get_settings
from libs.common.logging import get_logger
from libs.db.session import get_async_db
from services.payments_service.models import (
    Payment,
    PaymentStatus,
)
from services.payments_service.schemas import (
    MemberPaymentResponse,
)

settings = get_settings()
logger = get_logger(__name__)

FULFILLMENT_META_KEY = "fulfillment"
MAX_FULFILLMENT_RETRIES = 8
BASE_FULFILLMENT_RETRY_MINUTES = 2

from ._entitlement import _mark_paid_and_apply
from ._paystack import _to_kobo, _verify_paystack_transaction

router = APIRouter()


@router.get("/me", response_model=list[MemberPaymentResponse])
async def list_my_payments(
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    query = (
        select(Payment)
        .where(Payment.member_auth_id == current_user.user_id)
        .order_by(desc(Payment.created_at))
    )
    result = await db.execute(query)
    return result.scalars().all()


@router.post("/paystack/verify/{reference}", response_model=MemberPaymentResponse)
async def verify_my_paystack_payment(
    reference: str,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Verify a Paystack transaction and apply entitlements.
    Used as a fallback when webhooks are delayed; safe for production because we still
    verify the transaction status with Paystack before applying entitlements.
    """
    query = select(Payment).where(
        Payment.reference == reference,
        Payment.member_auth_id == current_user.user_id,
    )
    result = await db.execute(query)
    payment = result.scalar_one_or_none()
    if not payment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found"
        )

    if payment.status == PaymentStatus.PAID:
        if not payment.entitlement_applied_at:
            return await _mark_paid_and_apply(
                db=db,
                payment=payment,
                provider=payment.provider or "paystack",
                provider_reference=payment.provider_reference or reference,
                paid_at=payment.paid_at,
                provider_payload={"verify": "reapply_entitlement"},
            )
        return payment

    data = await _verify_paystack_transaction(reference)
    tx_status = str(data.get("status") or "").lower()
    if tx_status != "success":
        if payment.status != PaymentStatus.PAID:
            payment.status = PaymentStatus.FAILED
            payment.provider = "paystack"
            payment.provider_reference = reference
            payment.payment_metadata = {
                **(payment.payment_metadata or {}),
                "provider_payload": {"verify": data},
            }
            db.add(payment)
            await db.commit()
            await db.refresh(payment)

        # User-friendly error messages based on Paystack status
        error_messages = {
            "abandoned": "Payment was cancelled. You can try again when ready.",
            "failed": "Payment failed. Please try again or use a different payment method.",
            "pending": "Payment is still processing. Please wait a moment and refresh.",
            "reversed": "Payment was reversed. Please contact support if you believe this is an error.",
        }
        error_message = error_messages.get(
            tx_status, f"Payment was not completed (status: {tx_status or 'unknown'})"
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error_message,
        )

    amount_kobo = int(data.get("amount") or 0)
    expected_kobo = _to_kobo(payment.amount)
    if amount_kobo and expected_kobo and amount_kobo != expected_kobo:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Amount mismatch: got {amount_kobo}, expected {expected_kobo}.",
        )

    paid_at = None
    paid_at_str = data.get("paid_at")
    if isinstance(paid_at_str, str) and paid_at_str:
        try:
            paid_at = datetime.fromisoformat(paid_at_str.replace("Z", "+00:00"))
        except ValueError:
            paid_at = None

    return await _mark_paid_and_apply(
        db=db,
        payment=payment,
        provider="paystack",
        provider_reference=reference,
        paid_at=paid_at,
        provider_payload={"verify": data},
    )
