"""Service-to-service internal endpoints for payment initialization and verification."""

from datetime import datetime
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_service_role
from libs.auth.models import AuthUser
from libs.common.config import get_settings
from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from libs.db.session import get_async_db
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

router = APIRouter(prefix="/internal/payments", tags=["internal"])
settings = get_settings()
logger = get_logger(__name__)


@router.post(
    "/initialize",
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
    "/paystack/verify/{reference}",
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
    "/discounts/validate",
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


# ---------------------------------------------------------------------------
# Reporting: member payment summary
# ---------------------------------------------------------------------------


class MemberPaymentSummary(BaseModel):
    total_spent: int = 0
    payment_count: int = 0


@router.get(
    "/member-summary/{member_auth_id}",
    response_model=MemberPaymentSummary,
)
async def get_member_payment_summary(
    member_auth_id: str,
    date_from: datetime = Query(..., alias="from"),
    date_to: datetime = Query(..., alias="to"),
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Aggregate payment stats for a member within a date range.

    Used by the reporting service for quarterly reports.
    """
    from services.payments_service.models import Payment
    from services.payments_service.models.enums import PaymentStatus

    result = await db.execute(
        select(
            func.count(Payment.id).label("count"),
            func.coalesce(func.sum(Payment.amount), 0).label("total"),
        ).where(
            Payment.member_auth_id == member_auth_id,
            Payment.status == PaymentStatus.PAID,
            Payment.paid_at >= date_from,
            Payment.paid_at <= date_to,
        )
    )
    row = result.one()

    return MemberPaymentSummary(
        total_spent=int(row.total or 0),
        payment_count=row.count or 0,
    )


# ---------------------------------------------------------------------------
# Paystack proxy endpoints
#
# These are the canonical service-to-service path for any caller (e.g.
# members-service for the coach bank-account flow) to interact with Paystack.
# Centralising here keeps PAYSTACK_SECRET_KEY in a single service env and
# preserves the architectural rule that services never import each other's
# code directly.
# ---------------------------------------------------------------------------


class _BankItem(BaseModel):
    name: str
    code: str
    slug: str


class _ResolveAccountRequest(BaseModel):
    account_number: str
    bank_code: str


class _ResolveAccountResponse(BaseModel):
    account_number: str
    account_name: str
    bank_code: str


class _CreateRecipientRequest(BaseModel):
    name: str
    account_number: str
    bank_code: str
    currency: str = "NGN"


class _CreateRecipientResponse(BaseModel):
    recipient_code: str
    name: str
    account_number: str
    bank_code: str
    bank_name: str


@router.get(
    "/paystack/banks",
    response_model=list[_BankItem],
    dependencies=[Depends(require_service_role)],
    tags=["internal-paystack"],
)
async def internal_paystack_banks(country: str = "nigeria"):
    """Proxy Paystack's GET /bank for callers that don't carry the
    PAYSTACK_SECRET_KEY (e.g. members-service).
    """
    from services.payments_service.services.paystack_client import (
        PaystackClient,
        PaystackError,
    )

    try:
        paystack = PaystackClient()
    except ValueError as exc:
        logger.error("Paystack client init failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="Paystack is not configured on this service.",
        )

    try:
        banks = await paystack.list_banks(country=country)
    except PaystackError as exc:
        logger.error("Paystack list_banks failed: %s", exc.message)
        raise HTTPException(
            status_code=502, detail=f"Paystack list_banks failed: {exc.message}"
        )
    except Exception as exc:
        logger.exception("Unexpected error fetching banks")
        raise HTTPException(
            status_code=502,
            detail=f"Bank list unavailable ({type(exc).__name__})",
        )

    return [
        _BankItem(name=b.name, code=b.code, slug=b.slug or "")
        for b in banks
        if b.is_active
    ]


@router.post(
    "/paystack/resolve-account",
    response_model=_ResolveAccountResponse,
    dependencies=[Depends(require_service_role)],
    tags=["internal-paystack"],
)
async def internal_paystack_resolve_account(req: _ResolveAccountRequest):
    """Proxy Paystack's GET /bank/resolve."""
    from services.payments_service.services.paystack_client import (
        PaystackClient,
        PaystackError,
    )

    try:
        paystack = PaystackClient()
    except ValueError as exc:
        logger.error("Paystack client init failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="Paystack is not configured on this service.",
        )

    try:
        resolved = await paystack.resolve_account(
            account_number=req.account_number,
            bank_code=req.bank_code,
        )
    except PaystackError as exc:
        # Resolve failures are usually user errors (wrong account/bank) — 400.
        raise HTTPException(
            status_code=400,
            detail=f"Could not verify bank account: {exc.message}",
        )
    except Exception as exc:
        logger.exception("Unexpected error resolving account")
        raise HTTPException(
            status_code=502,
            detail=f"Account resolve unavailable ({type(exc).__name__})",
        )

    return _ResolveAccountResponse(
        account_number=resolved.account_number,
        account_name=resolved.account_name,
        bank_code=resolved.bank_code,
    )


@router.post(
    "/paystack/recipients",
    response_model=_CreateRecipientResponse,
    dependencies=[Depends(require_service_role)],
    tags=["internal-paystack"],
)
async def internal_paystack_create_recipient(req: _CreateRecipientRequest):
    """Proxy Paystack's POST /transferrecipient (nuban). Callers should
    have already verified the account via /resolve-account first.
    """
    from services.payments_service.services.paystack_client import (
        PaystackClient,
        PaystackError,
    )

    try:
        paystack = PaystackClient()
    except ValueError as exc:
        logger.error("Paystack client init failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="Paystack is not configured on this service.",
        )

    if (req.currency or "NGN").upper() != "NGN":
        # The PaystackClient currently hardcodes NGN. Accept the field for
        # forward-compat but reject anything else explicitly.
        raise HTTPException(status_code=400, detail="Only NGN recipients are supported")

    try:
        recipient = await paystack.create_transfer_recipient(
            name=req.name,
            account_number=req.account_number,
            bank_code=req.bank_code,
        )
    except PaystackError as exc:
        logger.error("Paystack create_transfer_recipient failed: %s", exc.message)
        raise HTTPException(
            status_code=400,
            detail=f"Could not create transfer recipient: {exc.message}",
        )
    except Exception as exc:
        logger.exception("Unexpected error creating recipient")
        raise HTTPException(
            status_code=502,
            detail=f"Recipient creation unavailable ({type(exc).__name__})",
        )

    return _CreateRecipientResponse(
        recipient_code=recipient.recipient_code,
        name=recipient.name,
        account_number=recipient.account_number,
        bank_code=recipient.bank_code,
        bank_name=recipient.bank_name,
    )
