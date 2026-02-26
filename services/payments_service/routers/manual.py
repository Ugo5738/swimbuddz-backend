"""Manual/bank transfer payment endpoints: proof submission and admin review."""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from libs.auth.dependencies import get_current_user, require_admin
from libs.auth.models import AuthUser
from libs.common.config import get_settings
from libs.common.emails.client import get_email_client
from libs.common.logging import get_logger
from libs.db.session import get_async_db
from services.payments_service.models import Payment, PaymentStatus
from services.payments_service.routers.intents import _apply_entitlement_with_tracking
from services.payments_service.schemas import (
    AdminReviewRequest,
    PaymentResponse,
    SubmitProofRequest,
)
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/payments", tags=["payments"])
settings = get_settings()
logger = get_logger(__name__)


@router.post("/{reference}/proof", response_model=PaymentResponse)
async def submit_proof_of_payment(
    reference: str,
    payload: SubmitProofRequest,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Submit proof of payment for a manual transfer payment.
    This updates the payment status to PENDING_REVIEW for admin approval.
    """
    result = await db.execute(
        select(Payment).where(
            Payment.reference == reference,
            Payment.member_auth_id == current_user.user_id,
        )
    )
    payment = result.scalar_one_or_none()

    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")

    if payment.payment_method != "manual_transfer":
        raise HTTPException(
            status_code=400, detail="Proof upload is only for manual transfer payments"
        )

    if payment.status not in [PaymentStatus.PENDING, PaymentStatus.FAILED]:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot upload proof for payment in status: {payment.status.value}",
        )

    payment.proof_of_payment_media_id = uuid.UUID(payload.proof_media_id)
    payment.status = PaymentStatus.PENDING_REVIEW
    payment.admin_review_note = None  # Clear any previous rejection note

    await db.commit()
    await db.refresh(payment)

    logger.info(f"Proof submitted for payment {reference}, status: PENDING_REVIEW")
    return payment


@router.get("/admin/pending-reviews", response_model=list[PaymentResponse])
async def list_pending_review_payments(
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    List all payments awaiting admin review (manual transfers with proof).
    Admin only.
    """
    result = await db.execute(
        select(Payment)
        .where(Payment.status == PaymentStatus.PENDING_REVIEW)
        .order_by(desc(Payment.created_at))
    )
    return result.scalars().all()


@router.post("/admin/{reference}/approve", response_model=PaymentResponse)
async def approve_manual_payment(
    reference: str,
    payload: AdminReviewRequest,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Approve a manual transfer payment after reviewing proof.
    This marks the payment as PAID and applies entitlements.
    Admin only.
    """
    result = await db.execute(select(Payment).where(Payment.reference == reference))
    payment = result.scalar_one_or_none()

    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")

    if payment.status != PaymentStatus.PENDING_REVIEW:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot approve payment in status: {payment.status.value}",
        )

    payment.status = PaymentStatus.PAID
    payment.provider = "manual_transfer"
    payment.paid_at = datetime.now(timezone.utc)
    payment.admin_review_note = payload.note

    await db.commit()
    await db.refresh(payment)

    logger.info(f"Payment {reference} approved by admin {current_user.email}")

    # Apply entitlements (same logic as Paystack webhook) with durable retries.
    await _apply_entitlement_with_tracking(payment)
    await db.commit()
    await db.refresh(payment)

    # Send email notification to member via centralized email service
    if payment.payer_email:
        try:
            email_client = get_email_client()
            await email_client.send_template(
                template_type="payment_approved",
                to_email=payment.payer_email,
                template_data={
                    "payment_reference": payment.reference,
                    "purpose": payment.purpose.value,
                    "amount": payment.amount,
                    "currency": payment.currency,
                },
            )
        except Exception as e:
            logger.warning(f"Failed to send approval email for {reference}: {e}")

    return payment


@router.post("/admin/{reference}/reject", response_model=PaymentResponse)
async def reject_manual_payment(
    reference: str,
    payload: AdminReviewRequest,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Reject a manual transfer payment (invalid proof).
    User can re-upload proof to try again.
    Admin only.
    """
    result = await db.execute(select(Payment).where(Payment.reference == reference))
    payment = result.scalar_one_or_none()

    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")

    if payment.status != PaymentStatus.PENDING_REVIEW:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot reject payment in status: {payment.status.value}",
        )

    # Set back to FAILED so user can re-upload
    payment.status = PaymentStatus.FAILED
    payment.admin_review_note = payload.note or "Payment proof rejected by admin"

    await db.commit()
    await db.refresh(payment)

    logger.info(f"Payment {reference} rejected by admin {current_user.email}")

    return payment
    logger.info(f"Payment {reference} rejected by admin {current_user.email}")

    return payment
