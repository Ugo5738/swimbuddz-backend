import hashlib
import hmac
import json
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from jose import jwt
from libs.auth.dependencies import get_current_user, require_admin
from libs.auth.models import AuthUser
from libs.common.config import get_settings
from libs.db.session import get_async_db
from services.payments_service.models import Payment, PaymentPurpose, PaymentStatus
from services.payments_service.schemas import (
    ClubBillingCycle,
    CompletePaymentRequest,
    CreatePaymentIntentRequest,
    PaymentIntentResponse,
    PaymentResponse,
)
from sqlalchemy import delete, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/payments", tags=["payments"])
settings = get_settings()


def _paystack_enabled() -> bool:
    key = (settings.PAYSTACK_SECRET_KEY or "").strip()
    return bool(key) and not key.startswith("your-")


def _paystack_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}",
        "Content-Type": "application/json",
    }


def _to_kobo(amount: float) -> int:
    value = Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return int(value * 100)


def _verify_paystack_signature(raw_body: bytes, signature: str) -> bool:
    secret = (settings.PAYSTACK_SECRET_KEY or "").encode("utf-8")
    digest = hmac.new(secret, raw_body, hashlib.sha512).hexdigest()
    return hmac.compare_digest(digest, signature)


def _callback_url(reference: str) -> str:
    if settings.PAYSTACK_CALLBACK_URL:
        return settings.PAYSTACK_CALLBACK_URL
    base = settings.FRONTEND_URL.rstrip("/")
    # Paystack appends `trxref` and `reference` query params automatically.
    # Avoid duplicating `reference` in our callback URL.
    return f"{base}/dashboard/billing?provider=paystack"


def _service_role_jwt() -> str:
    now = int(datetime.now(tz=timezone.utc).timestamp())
    payload = {
        "sub": "service:payments",
        "email": settings.ADMIN_EMAIL,
        "role": "service_role",
        "iat": now,
        "exp": now + 60,
    }
    return jwt.encode(payload, settings.SUPABASE_JWT_SECRET, algorithm="HS256")


async def _initialize_paystack(
    payment: Payment, email: str
) -> tuple[str | None, str | None]:
    """
    Initialize a Paystack transaction and return (authorization_url, access_code).
    """
    if not _paystack_enabled():
        return None, None

    payload = {
        "email": email,
        "amount": _to_kobo(payment.amount),
        "currency": payment.currency,
        "reference": payment.reference,
        "callback_url": _callback_url(payment.reference),
        "metadata": {
            "internal_reference": payment.reference,
            "purpose": str(payment.purpose),
            "member_auth_id": payment.member_auth_id,
        },
    }
    # In local/dev, limit channels to avoid flaky/unsupported methods (e.g. QR/Zap)
    # that can leave the checkout stuck on "transaction ongoing" without completing.
    if settings.ENVIRONMENT in ("local", "development"):
        payload["channels"] = ["card", "bank", "ussd", "bank_transfer"]

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{settings.PAYSTACK_API_BASE_URL.rstrip('/')}/transaction/initialize",
            headers=_paystack_headers(),
            json=payload,
        )

    if resp.status_code >= 400:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Paystack initialize failed ({resp.status_code}): {resp.text}",
        )

    body = resp.json()
    if not body.get("status"):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Paystack initialize failed: {body}",
        )

    data = body.get("data") or {}
    return data.get("authorization_url"), data.get("access_code")


async def _verify_paystack_transaction(reference: str) -> dict:
    if not _paystack_enabled():
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Paystack is not configured.",
        )

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{settings.PAYSTACK_API_BASE_URL.rstrip('/')}/transaction/verify/{reference}",
            headers=_paystack_headers(),
        )

    if resp.status_code >= 400:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Paystack verify failed ({resp.status_code}): {resp.text}",
        )

    body = resp.json()
    if not body.get("status"):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Paystack verify failed: {body}",
        )
    return body.get("data") or {}


async def _apply_entitlement(payment: Payment) -> None:
    if payment.purpose == PaymentPurpose.COMMUNITY_ANNUAL:
        path = f"/admin/members/by-auth/{payment.member_auth_id}/community/activate"
        years = int((payment.payment_metadata or {}).get("years") or 1)
        payload = {"years": years}
    elif payment.purpose == PaymentPurpose.CLUB_MONTHLY:
        path = f"/admin/members/by-auth/{payment.member_auth_id}/club/activate"
        months = int((payment.payment_metadata or {}).get("months") or 1)
        payload = {"months": months}
    else:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=f"Entitlement application not implemented for purpose={payment.purpose}",
        )

    headers = {"Authorization": f"Bearer {_service_role_jwt()}"}
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{settings.MEMBERS_SERVICE_URL}{path}", json=payload, headers=headers
        )
        if resp.status_code >= 400:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to apply entitlement via members_service ({resp.status_code}): {resp.text}",
            )


async def _mark_paid_and_apply(
    db: AsyncSession,
    payment: Payment,
    provider: str,
    provider_reference: str | None,
    paid_at: datetime | None,
    provider_payload: dict | None = None,
) -> Payment:
    if payment.status == PaymentStatus.PAID:
        return payment

    payment.status = PaymentStatus.PAID
    payment.provider = provider
    payment.provider_reference = provider_reference
    payment.paid_at = paid_at or datetime.now(timezone.utc)
    if provider_payload:
        payment.payment_metadata = {
            **(payment.payment_metadata or {}),
            "provider_payload": provider_payload,
        }

    db.add(payment)
    await db.commit()
    await db.refresh(payment)

    try:
        await _apply_entitlement(payment)
        payment.entitlement_applied_at = datetime.now(timezone.utc)
        payment.entitlement_error = None
    except Exception as e:
        payment.entitlement_error = str(e)

    db.add(payment)
    await db.commit()
    await db.refresh(payment)
    return payment


@router.post(
    "/intents",
    response_model=PaymentIntentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_payment_intent(
    payload: CreatePaymentIntentRequest,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Create a payment intent (records a pending payment) and (if configured) initializes Paystack checkout.
    """
    if payload.purpose == PaymentPurpose.COMMUNITY_ANNUAL:
        amount = float(
            getattr(settings, "COMMUNITY_ANNUAL_FEE_NGN", 5000) * payload.years
        )
        payment_metadata = {**(payload.payment_metadata or {}), "years": payload.years}
    elif payload.purpose == PaymentPurpose.CLUB_MONTHLY:
        cycle = payload.club_billing_cycle or ClubBillingCycle.MONTHLY
        if cycle == ClubBillingCycle.ANNUAL:
            amount = float(getattr(settings, "CLUB_ANNUAL_FEE_NGN", 150000))
            months = 12
        elif cycle == ClubBillingCycle.BIANNUAL:
            amount = float(getattr(settings, "CLUB_BIANNUAL_FEE_NGN", 80000))
            months = 6
        elif cycle == ClubBillingCycle.QUARTERLY:
            amount = float(getattr(settings, "CLUB_QUARTERLY_FEE_NGN", 42500))
            months = 3
        else:
            club_fee = int(getattr(settings, "CLUB_MONTHLY_FEE_NGN", 15000))
            months = payload.months
            amount = float(club_fee * months)

        payment_metadata = {
            **(payload.payment_metadata or {}),
            "months": months,
            "club_billing_cycle": str(cycle),
        }
    else:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=f"Payment intent not implemented for purpose={payload.purpose}",
        )

    payment = Payment(
        reference=Payment.generate_reference(),
        member_auth_id=current_user.user_id,
        payer_email=current_user.email,
        purpose=payload.purpose,
        amount=amount,
        currency=payload.currency,
        status=PaymentStatus.PENDING,
        payment_metadata=payment_metadata,
    )

    db.add(payment)
    await db.commit()
    await db.refresh(payment)

    checkout_url = None
    if _paystack_enabled():
        if not current_user.email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Authenticated user email is required to initialize Paystack",
            )
        authorization_url, access_code = await _initialize_paystack(
            payment, current_user.email
        )
        checkout_url = authorization_url
        payment.provider = "paystack"
        payment.provider_reference = payment.reference
        payment.payment_metadata = {
            **(payment.payment_metadata or {}),
            "paystack": {
                "authorization_url": authorization_url,
                "access_code": access_code,
            },
        }
        db.add(payment)
        await db.commit()
        await db.refresh(payment)

    return PaymentIntentResponse(
        reference=payment.reference,
        amount=payment.amount,
        currency=payment.currency,
        purpose=payment.purpose,
        status=payment.status,
        checkout_url=checkout_url,
        created_at=payment.created_at,
    )


@router.delete("/admin/members/by-auth/{auth_id}")
async def admin_delete_member_payments(
    auth_id: str,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Delete payments for a member by auth ID (Admin only).
    """
    result = await db.execute(delete(Payment).where(Payment.member_auth_id == auth_id))
    await db.commit()
    return {"deleted": result.rowcount or 0}


@router.get("/me", response_model=list[PaymentResponse])
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


@router.post("/paystack/verify/{reference}", response_model=PaymentResponse)
async def verify_my_paystack_payment(
    reference: str,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Verify a Paystack transaction and apply entitlements.
    Dev-only fallback for local/dev where Paystack webhooks may not reach the backend.
    """
    if settings.ENVIRONMENT == "production":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Payment not successful (status={tx_status or 'unknown'}).",
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


@router.post("/{reference}/complete", response_model=PaymentResponse)
async def complete_payment(
    reference: str,
    payload: CompletePaymentRequest,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Mark a payment as paid and apply the corresponding member entitlement.
    In production, this should be triggered by a verified payment webhook.
    """
    query = select(Payment).where(Payment.reference == reference)
    result = await db.execute(query)
    payment = result.scalar_one_or_none()

    if not payment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found"
        )

    if payment.status == PaymentStatus.PAID:
        return payment

    if payload.provider_reference:
        dupe_query = select(Payment).where(
            Payment.provider_reference == payload.provider_reference
        )
        dupe_result = await db.execute(dupe_query)
        dupe_payment = dupe_result.scalar_one_or_none()
        if dupe_payment and dupe_payment.id != payment.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="provider_reference already used by another payment",
            )

    payment.status = PaymentStatus.PAID
    payment.provider = payload.provider
    payment.provider_reference = payload.provider_reference
    payment.paid_at = payload.paid_at or datetime.now(timezone.utc)
    payment.entitlement_error = None

    if payload.note:
        payment.payment_metadata = {
            **(payment.payment_metadata or {}),
            "admin_note": payload.note,
        }

    db.add(payment)
    await db.commit()
    await db.refresh(payment)

    try:
        await _apply_entitlement(payment)
        payment.entitlement_applied_at = datetime.now(timezone.utc)
        payment.entitlement_error = None
    except Exception as e:
        payment.entitlement_error = str(e)

    db.add(payment)
    await db.commit()
    await db.refresh(payment)
    return payment


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
