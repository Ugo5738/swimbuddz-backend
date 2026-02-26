"""Paystack webhook handler, reference generation, and listing."""

import json
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.common.config import get_settings
from libs.common.logging import get_logger
from libs.common.service_client import internal_post
from libs.db.session import get_async_db
from services.payments_service.models import (
    CoachPayout,
    Payment,
    PaymentPurpose,
    PaymentStatus,
    PayoutStatus,
)
from services.payments_service.routers.intents import (
    _mark_paid_and_apply,
    _to_kobo,
    _verify_paystack_signature,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/payments", tags=["payments"])
settings = get_settings()
logger = get_logger(__name__)


@router.post("/webhooks/paystack")
async def paystack_webhook(
    request: Request,
    db: AsyncSession = Depends(get_async_db),
):
    """
    Paystack webhook endpoint (no auth; verified by x-paystack-signature).
    """
    raw = await request.body()
    signature = request.headers.get("x-paystack-signature")
    if not signature or not _verify_paystack_signature(raw, signature):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid signature"
        )

    payload = json.loads(raw.decode("utf-8") or "{}")
    event = payload.get("event")
    data = payload.get("data") or {}
    reference = data.get("reference")
    if not reference:
        return {"received": True}

    query = select(Payment).where(Payment.reference == reference)
    result = await db.execute(query)
    payment = result.scalar_one_or_none()
    if not payment:
        # Check if this is a wallet topup (no Payment record — wallet service owns lifecycle)
        metadata = data.get("metadata") or {}
        if metadata.get("type") == "wallet_topup" and event in (
            "charge.success",
            "charge.failed",
            "transaction.failed",
        ):
            topup_status = "completed" if event == "charge.success" else "failed"
            try:
                resp = await internal_post(
                    service_url=settings.WALLET_SERVICE_URL,
                    path="/internal/wallet/confirm-topup",
                    calling_service="payments",
                    json={
                        "topup_reference": reference,
                        "payment_reference": reference,
                        "status": topup_status,
                    },
                )
                if resp.status_code >= 400:
                    logger.error(
                        "Wallet topup confirm failed for %s with status=%s (http %d): %s",
                        reference,
                        topup_status,
                        resp.status_code,
                        resp.text,
                    )
                else:
                    logger.info(
                        "Wallet topup processed for %s with status=%s (http %d)",
                        reference,
                        topup_status,
                        resp.status_code,
                    )
            except Exception as e:
                logger.error("Failed to confirm wallet topup %s: %s", reference, e)
            return {"received": True}

        logger.warning(
            f"Webhook received for unknown payment reference: {reference}",
            extra={"extra_fields": {"reference": reference, "event": event}},
        )
        return {"received": True}

    # IDEMPOTENCY CHECK: Skip if payment is already fully processed
    if payment.status == PaymentStatus.PAID and payment.entitlement_applied_at:
        logger.info(
            f"Webhook for {reference} skipped - payment already processed",
            extra={
                "extra_fields": {
                    "payment_id": str(payment.id),
                    "reference": reference,
                    "event": event,
                }
            },
        )
        return {"received": True}

    if event == "charge.success":
        amount_kobo = int(data.get("amount") or 0)
        expected_kobo = _to_kobo(payment.amount)
        if amount_kobo and expected_kobo and amount_kobo != expected_kobo:
            payment.entitlement_error = (
                f"Paystack amount mismatch: got {amount_kobo}, expected {expected_kobo}"
            )
            payment.payment_metadata = {
                **(payment.payment_metadata or {}),
                "paystack": {
                    **((payment.payment_metadata or {}).get("paystack") or {}),
                    "amount_kobo": amount_kobo,
                },
            }
            db.add(payment)
            await db.commit()
            return {"received": True}

        paid_at_str = data.get("paid_at")
        paid_at = None
        if isinstance(paid_at_str, str) and paid_at_str:
            try:
                paid_at = datetime.fromisoformat(paid_at_str.replace("Z", "+00:00"))
            except ValueError:
                paid_at = None

        await _mark_paid_and_apply(
            db=db,
            payment=payment,
            provider="paystack",
            provider_reference=reference,
            paid_at=paid_at,
            provider_payload={"event": event, "data": data},
        )
        return {"received": True}

    if event in ("charge.failed", "transaction.failed"):
        if payment.status != PaymentStatus.PAID:
            payment.status = PaymentStatus.FAILED
            payment.provider = "paystack"
            payment.provider_reference = reference
            payment.payment_metadata = {
                **(payment.payment_metadata or {}),
                "provider_payload": {"event": event, "data": data},
            }
            db.add(payment)
            await db.commit()

            # For ACADEMY_COHORT payments: notify the student that their access
            # is suspended because the installment payment failed.
            if payment.purpose == PaymentPurpose.ACADEMY_COHORT:
                try:
                    from libs.auth.dependencies import _service_role_jwt
                    from libs.common.emails.client import get_email_client

                    enrollment_id = (payment.payment_metadata or {}).get(
                        "enrollment_id"
                    )
                    installment_number = (payment.payment_metadata or {}).get(
                        "installment_number"
                    )
                    total_installments = (payment.payment_metadata or {}).get(
                        "total_installments"
                    )
                    member_email = payment.payer_email
                    member_name = "Student"

                    # Fetch member details for a personalised email
                    svc_headers = {
                        "Authorization": f"Bearer {_service_role_jwt('payments')}"
                    }
                    async with httpx.AsyncClient(timeout=30) as client:
                        member_resp = await client.get(
                            f"{settings.MEMBERS_SERVICE_URL}/members/by-auth/{payment.member_auth_id}",
                            headers=svc_headers,
                        )
                        if member_resp.status_code < 400:
                            member_data = member_resp.json()
                            member_email = member_data.get("email") or member_email
                            member_name = member_data.get("first_name", "Student")

                    if member_email:
                        email_client = get_email_client()
                        await email_client.send_template(
                            template_type="academy_access_suspended",
                            to_email=member_email,
                            template_data={
                                "member_name": member_name,
                                "installment_number": (
                                    int(installment_number)
                                    if installment_number
                                    else None
                                ),
                                "total_installments": (
                                    int(total_installments)
                                    if total_installments
                                    else None
                                ),
                                "amount": payment.amount,
                                "currency": payment.currency,
                                "payment_reference": payment.reference,
                                "enrollment_id": enrollment_id,
                            },
                        )
                        logger.info(
                            f"Sent access-suspended notification to {member_email} "
                            f"for failed installment payment {payment.reference}"
                        )
                except Exception as e:
                    # Non-fatal — webhook must still return 200
                    logger.error(
                        f"Failed to send access-suspended notification for {payment.reference}: {e}"
                    )
        return {"received": True}

    # Handle transfer events for coach payouts
    if event == "transfer.success":
        # Update payout status to PAID

        transfer_reference = data.get("reference")
        transfer_code = data.get("transfer_code")

        if transfer_reference:
            payout_result = await db.execute(
                select(CoachPayout).where(
                    CoachPayout.payment_reference == transfer_reference
                )
            )
            payout = payout_result.scalar_one_or_none()

            if payout:
                payout.status = PayoutStatus.PAID
                payout.paystack_transfer_status = "success"
                payout.paid_at = datetime.now(timezone.utc)
                db.add(payout)
                await db.commit()
                logger.info(
                    f"Payout {payout.id} marked as paid via transfer webhook",
                    extra={"extra_fields": {"transfer_code": transfer_code}},
                )
        return {"received": True}

    if event == "transfer.failed":
        # Update payout status to FAILED
        transfer_reference = data.get("reference")
        failure_reason = data.get("reason") or data.get("message") or "Unknown error"

        if transfer_reference:
            payout_result = await db.execute(
                select(CoachPayout).where(
                    CoachPayout.payment_reference == transfer_reference
                )
            )
            payout = payout_result.scalar_one_or_none()

            if payout:
                payout.status = PayoutStatus.FAILED
                payout.paystack_transfer_status = "failed"
                payout.failure_reason = failure_reason
                db.add(payout)
                await db.commit()
                logger.warning(
                    f"Payout {payout.id} transfer failed: {failure_reason}",
                    extra={"extra_fields": {"transfer_reference": transfer_reference}},
                )
        return {"received": True}

    return {"received": True}


@router.post("/generate-reference")
async def generate_payment_reference(current_user: AuthUser = Depends(require_admin)):
    """
    Backwards-compat helper.
    """
    return {"reference": Payment.generate_reference()}


@router.get("/", dependencies=[Depends(require_admin)])
async def list_payments_admin():
    return {
        "message": "Use /payments/me for member view; admin listing not implemented yet."
    }
