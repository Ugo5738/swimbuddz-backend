"""Service-to-service internal endpoints for payment initialization and verification."""

from datetime import datetime
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from libs.auth.dependencies import require_service_role
from libs.common.config import get_settings
from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from libs.db.session import get_async_db
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.payments_service.models import (
    Discount,
    DiscountType,
    Payment,
    PaymentPurpose,
    PaymentStatus,
)
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


# ---------------------------------------------------------------------------
# Discount validation for cross-service use
# ---------------------------------------------------------------------------


class InternalDiscountValidateRequest(BaseModel):
    code: str
    purpose: str = "store_order"
    amount: float = 0
    member_auth_id: Optional[str] = None


class InternalDiscountValidateResponse(BaseModel):
    valid: bool
    code: str
    discount_type: Optional[str] = None  # "percentage" or "fixed"
    value: Optional[float] = None  # Percentage (0-100) or fixed amount
    discount_amount: Optional[float] = None  # Calculated discount for given amount
    message: Optional[str] = None


@router.post(
    "/internal/discounts/validate",
    response_model=InternalDiscountValidateResponse,
    dependencies=[Depends(require_service_role)],
    tags=["internal-payments"],
)
async def internal_validate_discount(
    req: InternalDiscountValidateRequest,
    db: AsyncSession = Depends(get_async_db),
):
    """Validate a discount code for another service (e.g., store).

    Returns discount details and calculated discount amount if valid.
    Raises 400 if the code is invalid, expired, or exhausted.
    """
    query = select(Discount).where(
        Discount.code == req.code.upper().strip(),
        Discount.is_active.is_(True),
    )
    result = await db.execute(query)
    discount = result.scalar_one_or_none()

    if not discount:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid discount code: {req.code}",
        )

    now = utc_now()

    if discount.valid_from and discount.valid_from > now:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Discount code is not yet active",
        )
    if discount.valid_until and discount.valid_until < now:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Discount code has expired",
        )

    if discount.max_uses and discount.current_uses >= discount.max_uses:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Discount code has reached its usage limit",
        )

    # Check purpose applicability
    applicable_purposes = [p.upper() for p in (discount.applies_to or [])]
    if applicable_purposes and req.purpose.upper() not in applicable_purposes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Discount code does not apply to {req.purpose}",
        )

    # Calculate discount amount
    discount_amount = 0.0
    if req.amount > 0:
        if discount.discount_type == DiscountType.PERCENTAGE:
            discount_amount = round(req.amount * (discount.value / 100), 2)
        elif discount.discount_type == DiscountType.FIXED:
            discount_amount = min(discount.value, req.amount)

    return InternalDiscountValidateResponse(
        valid=True,
        code=discount.code,
        discount_type=discount.discount_type.value,
        value=discount.value,
        discount_amount=discount_amount,
        message=None,
    )
