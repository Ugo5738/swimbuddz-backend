"""Service-to-service internal endpoints for payment initialization and verification."""

from datetime import datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from libs.auth.dependencies import require_service_role
from libs.common.config import get_settings
from libs.common.logging import get_logger
from libs.db.session import get_async_db
from services.payments_service.models import Payment, PaymentPurpose, PaymentStatus
from services.payments_service.routers.intents import (
    _callback_url,
    _paystack_enabled,
    _paystack_headers,
    _to_kobo,
    _verify_paystack_transaction,
)
from services.payments_service.schemas import (
    InternalInitializeRequest,
    InternalInitializeResponse,
    InternalPaystackVerifyResponse,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/payments", tags=["payments"])
settings = get_settings()
logger = get_logger(__name__)


@router.post(
    "/internal/initialize",
    response_model=InternalInitializeResponse,
    dependencies=[Depends(require_service_role)],
    tags=["internal-payments"],
)
async def internal_initialize_payment(
    req: InternalInitializeRequest,
    db: AsyncSession = Depends(get_async_db),
):
    """Initialize a Paystack transaction on behalf of another service.

    Called by wallet_service for topups, or any service needing Paystack.
    If purpose maps to PaymentPurpose, a Payment intent record is persisted
    for unified reconciliation/forensics.

    Auth: service-role JWT only (via ``require_service_role``).
    """
    if not _paystack_enabled():
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Paystack is not configured.",
        )

    payer_email = None
    if isinstance(req.metadata, dict):
        candidate = req.metadata.get("payer_email")
        if isinstance(candidate, str):
            candidate = candidate.strip()
            if candidate:
                payer_email = candidate

    purpose_enum: PaymentPurpose | None = None
    try:
        purpose_enum = PaymentPurpose(str(req.purpose).lower())
    except ValueError:
        purpose_enum = None

    payment: Payment | None = None
    if purpose_enum:
        existing = await db.execute(
            select(Payment).where(Payment.reference == req.reference)
        )
        payment = existing.scalar_one_or_none()
        if not payment:
            payment = Payment(
                reference=req.reference,
                member_auth_id=req.member_auth_id,
                payer_email=payer_email,
                purpose=purpose_enum,
                amount=req.amount,
                currency=req.currency,
                status=PaymentStatus.PENDING,
                provider="paystack",
                provider_reference=req.reference,
                payment_method="paystack",
                payment_metadata={
                    **(req.metadata or {}),
                    "internal_reference": req.reference,
                    "purpose": req.purpose,
                    "member_auth_id": req.member_auth_id,
                },
            )
            db.add(payment)
            await db.commit()
        elif payer_email and payment.payer_email != payer_email:
            payment.payer_email = payer_email
            await db.commit()

    # Build callback URL
    callback = _callback_url(req.reference, req.callback_url)

    # Build Paystack payload
    paystack_email = payer_email or (payment.payer_email if payment else None)
    payload = {
        "email": paystack_email or settings.ADMIN_EMAIL or "noreply@swimbuddz.com",
        "amount": _to_kobo(req.amount),
        "currency": req.currency,
        "reference": req.reference,
        "callback_url": callback,
        "metadata": {
            **(req.metadata or {}),
            "purpose": req.purpose,
            "member_auth_id": req.member_auth_id,
        },
    }
    # In local/dev, limit channels to avoid flaky/unsupported methods
    if settings.ENVIRONMENT in ("local", "development"):
        payload["channels"] = ["card", "bank", "ussd", "bank_transfer"]

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{settings.PAYSTACK_API_BASE_URL.rstrip('/')}/transaction/initialize",
                headers=_paystack_headers(),
                json=payload,
            )
    except httpx.RequestError as exc:
        logger.error("Paystack connection failed for %s: %s", req.reference, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not reach payment provider. Please try again.",
        )

    if resp.status_code >= 400:
        logger.error("Paystack init failed for %s: %s", req.reference, resp.text)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Paystack initialize failed ({resp.status_code})",
        )

    body = resp.json()
    if not body.get("status"):
        logger.error("Paystack init rejected for %s: %s", req.reference, body)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Paystack initialize failed",
        )

    data = body.get("data") or {}
    return InternalInitializeResponse(
        reference=req.reference,
        authorization_url=data.get("authorization_url"),
        access_code=data.get("access_code"),
    )


@router.get(
    "/internal/paystack/verify/{reference}",
    response_model=InternalPaystackVerifyResponse,
    dependencies=[Depends(require_service_role)],
    tags=["internal-payments"],
)
async def internal_verify_paystack_reference(reference: str):
    """Verify a Paystack reference for internal fulfillment reconciliation."""
    data = await _verify_paystack_transaction(reference)
    provider_status = str((data.get("status") or "")).lower()

    if provider_status == "success":
        status = "completed"
    elif provider_status in {"failed", "abandoned", "reversed"}:
        status = "failed"
    elif provider_status in {"pending", "ongoing", "processing", "queued"}:
        status = "pending"
    else:
        status = "unknown"

    paid_at = None
    paid_at_str = data.get("paid_at")
    if isinstance(paid_at_str, str) and paid_at_str:
        try:
            paid_at = datetime.fromisoformat(paid_at_str.replace("Z", "+00:00"))
        except ValueError:
            paid_at = None

    return InternalPaystackVerifyResponse(
        reference=reference,
        status=status,
        provider_status=provider_status or None,
        paid_at=paid_at,
        amount_kobo=data.get("amount"),
        currency=data.get("currency"),
        raw=data,
    )
