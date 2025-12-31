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
from services.payments_service.models import (
    Discount,
    DiscountType,
    Payment,
    PaymentPurpose,
    PaymentStatus,
)
from services.payments_service.schemas import (
    ClubBillingCycle,
    CompletePaymentRequest,
    CreatePaymentIntentRequest,
    DiscountCreate,
    DiscountResponse,
    DiscountUpdate,
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


async def _update_pending_payment_reference(
    auth_id: str, reference: str | None
) -> None:
    """Update or clear the pending_payment_reference on a member's membership."""
    headers = {"Authorization": f"Bearer {_service_role_jwt()}"}
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.patch(
            f"{settings.MEMBERS_SERVICE_URL}/admin/members/by-auth/{auth_id}/membership",
            json={"pending_payment_reference": reference},
            headers=headers,
        )
        # Ignore failures - this is a best-effort feature
        if resp.status_code >= 400:
            import logging

            logging.getLogger(__name__).warning(
                f"Failed to update pending_payment_reference for {auth_id}: {resp.status_code}"
            )


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
    # Handle Community activation
    if payment.purpose == PaymentPurpose.COMMUNITY:
        path = f"/admin/members/by-auth/{payment.member_auth_id}/community/activate"
        years = int((payment.payment_metadata or {}).get("years") or 1)
        payload = {"years": years}

    # Handle Club add-on (may include community extension)
    elif payment.purpose == PaymentPurpose.CLUB:
        months = int((payment.payment_metadata or {}).get("months") or 1)
        community_extension_months = int((payment.payment_metadata or {}).get("community_extension_months") or 0)
        
        headers = {"Authorization": f"Bearer {_service_role_jwt()}"}
        async with httpx.AsyncClient(timeout=30) as client:
            # If community extension was included, extend Community first
            if community_extension_months > 0:
                # Calculate years/months for community extension
                ext_years = community_extension_months // 12
                ext_months = community_extension_months % 12
                community_resp = await client.post(
                    f"{settings.MEMBERS_SERVICE_URL}/admin/members/by-auth/{payment.member_auth_id}/community/extend",
                    json={"months": community_extension_months},
                    headers=headers,
                )
                if community_resp.status_code >= 400:
                    logger.warning(f"Failed to extend community: {community_resp.text}")
            
            # Activate Club
            club_resp = await client.post(
                f"{settings.MEMBERS_SERVICE_URL}/admin/members/by-auth/{payment.member_auth_id}/club/activate",
                json={"months": months},
                headers=headers,
            )
            if club_resp.status_code >= 400:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"Failed to apply club entitlement via members_service ({club_resp.status_code}): {club_resp.text}",
                )
        # Clear pending payment reference on success
        await _update_pending_payment_reference(payment.member_auth_id, None)
        return

    # Handle Club bundle (Community + Club)
    elif payment.purpose == PaymentPurpose.CLUB_BUNDLE:
        years = int((payment.payment_metadata or {}).get("years") or 1)
        months = int((payment.payment_metadata or {}).get("months") or 1)
        headers = {"Authorization": f"Bearer {_service_role_jwt()}"}
        async with httpx.AsyncClient(timeout=30) as client:
            community_resp = await client.post(
                f"{settings.MEMBERS_SERVICE_URL}/admin/members/by-auth/{payment.member_auth_id}/community/activate",
                json={"years": years},
                headers=headers,
            )
            if community_resp.status_code >= 400:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"Failed to apply community entitlement via members_service ({community_resp.status_code}): {community_resp.text}",
                )
            club_resp = await client.post(
                f"{settings.MEMBERS_SERVICE_URL}/admin/members/by-auth/{payment.member_auth_id}/club/activate",
                json={"months": months, "skip_community_check": True},
                headers=headers,
            )
            if club_resp.status_code >= 400:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"Failed to apply club entitlement via members_service ({club_resp.status_code}): {club_resp.text}",
                )
        # Clear pending payment reference on success
        await _update_pending_payment_reference(payment.member_auth_id, None)
        return

    # Handle Academy cohort enrollment
    elif payment.purpose == PaymentPurpose.ACADEMY_COHORT:
        enrollment_id = (payment.payment_metadata or {}).get("enrollment_id")
        if not enrollment_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="enrollment_id missing in payment metadata",
            )
        headers = {"Authorization": f"Bearer {_service_role_jwt()}"}
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{settings.ACADEMY_SERVICE_URL}/admin/enrollments/{enrollment_id}/mark-paid",
                headers=headers,
            )
            if resp.status_code >= 400:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"Failed to mark enrollment as paid ({resp.status_code}): {resp.text}",
                )
        # Clear pending payment reference on success
        await _update_pending_payment_reference(payment.member_auth_id, None)
        return

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
    # Clear pending payment reference on success
    await _update_pending_payment_reference(payment.member_auth_id, None)


def _resolve_club_amount(
    payload: CreatePaymentIntentRequest,
) -> tuple[float, int, ClubBillingCycle]:
    # Default to quarterly (monthly removed)
    cycle = payload.club_billing_cycle or ClubBillingCycle.QUARTERLY
    if cycle == ClubBillingCycle.ANNUAL:
        amount = float(getattr(settings, "CLUB_ANNUAL_FEE_NGN", 150000))
        months = 12
    elif cycle == ClubBillingCycle.BIANNUAL:
        amount = float(getattr(settings, "CLUB_BIANNUAL_FEE_NGN", 80000))
        months = 6
    else:  # QUARTERLY (default)
        amount = float(getattr(settings, "CLUB_QUARTERLY_FEE_NGN", 42500))
        months = 3
    return amount, months, cycle


async def _validate_and_apply_discount(
    db: AsyncSession,
    discount_code: str | None,
    purpose: PaymentPurpose,
    original_amount: float,
    member_auth_id: str,
) -> tuple[float, float | None, Discount | None]:
    """
    Validate and apply a discount code if provided.
    Returns: (final_amount, discount_applied, discount_obj)
    """
    if not discount_code:
        return original_amount, None, None

    from libs.common.datetime_utils import utc_now

    # Lookup discount code
    query = select(Discount).where(
        Discount.code == discount_code.upper().strip(),
        Discount.is_active.is_(True),
    )
    result = await db.execute(query)
    discount = result.scalar_one_or_none()

    if not discount:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid discount code: {discount_code}",
        )

    now = utc_now()

    # Check validity period
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

    # Check usage limits
    if discount.max_uses and discount.current_uses >= discount.max_uses:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Discount code has reached its usage limit",
        )

    # Check if discount applies to this payment purpose
    if discount.applies_to:
        applicable_purposes = [p.upper() for p in discount.applies_to]
        if purpose.value.upper() not in applicable_purposes:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Discount code does not apply to {purpose.value} payments",
            )

    # Calculate discount amount
    if discount.discount_type == DiscountType.PERCENTAGE:
        discount_amount = original_amount * (discount.value / 100)
    else:  # FIXED
        discount_amount = discount.value

    # Ensure discount doesn't exceed original amount
    discount_amount = min(discount_amount, original_amount)
    final_amount = max(original_amount - discount_amount, 0)

    # Increment usage count
    discount.current_uses += 1
    db.add(discount)

    return final_amount, discount_amount, discount


async def _mark_paid_and_apply(
    db: AsyncSession,
    payment: Payment,
    provider: str,
    provider_reference: str | None,
    paid_at: datetime | None,
    provider_payload: dict | None = None,
) -> Payment:
    # Allow re-applying entitlements if the payment is already marked paid
    # but entitlement_applied_at is still missing.
    if payment.status == PaymentStatus.PAID and payment.entitlement_applied_at:
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
    # Community activation - ₦20,000/year
    if payload.purpose == PaymentPurpose.COMMUNITY:
        amount = float(
            getattr(settings, "COMMUNITY_ANNUAL_FEE_NGN", 20000) * payload.years
        )
        payment_metadata = {**(payload.payment_metadata or {}), "years": payload.years}

    # Club add-on - check if community extension needed
    elif payload.purpose == PaymentPurpose.CLUB:
        amount, months, cycle = _resolve_club_amount(payload)
        
        # Check if Club would exceed Community membership
        community_extension_months = 0
        community_extension_amount = 0.0
        requires_community_extension = False
        
        # Fetch member's community_paid_until from members_service
        try:
            headers = {"Authorization": f"Bearer {_service_role_jwt()}"}
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(
                    f"{settings.MEMBERS_SERVICE_URL}/members/by-auth/{current_user.user_id}",
                    headers=headers,
                )
                if resp.status_code == 200:
                    member_data = resp.json()
                    membership = member_data.get("membership") or {}
                    community_until_str = membership.get("community_paid_until")
                    
                    if community_until_str:
                        from libs.common.datetime_utils import utc_now
                        from dateutil.relativedelta import relativedelta
                        
                        community_until = datetime.fromisoformat(community_until_str.replace("Z", "+00:00"))
                        club_end = utc_now() + relativedelta(months=months)
                        
                        if club_end > community_until:
                            # Calculate months needed to extend Community
                            diff_days = (club_end - community_until).days
                            community_extension_months = max(1, (diff_days + 29) // 30)  # Round up
                            community_monthly_rate = getattr(settings, "COMMUNITY_ANNUAL_FEE_NGN", 20000) / 12
                            community_extension_amount = round(community_monthly_rate * community_extension_months, 2)
                            requires_community_extension = True
        except Exception as e:
            logger.warning(f"Could not check community status: {e}")
        
        # If extension required and user opted in, add to total
        if requires_community_extension and payload.include_community_extension:
            amount += community_extension_amount
        
        payment_metadata = {
            **(payload.payment_metadata or {}),
            "months": months,
            "club_billing_cycle": str(cycle),
            "community_extension_months": community_extension_months if payload.include_community_extension else 0,
            "community_extension_amount": community_extension_amount if payload.include_community_extension else 0,
        }

    # Club bundle - Community + Club together
    elif payload.purpose == PaymentPurpose.CLUB_BUNDLE:
        community_fee = float(
            getattr(settings, "COMMUNITY_ANNUAL_FEE_NGN", 20000) * payload.years
        )
        club_amount, months, cycle = _resolve_club_amount(payload)
        amount = community_fee + club_amount
        payment_metadata = {
            **(payload.payment_metadata or {}),
            "years": payload.years,
            "months": months,
            "club_billing_cycle": str(cycle),
            "components": {
                "community": community_fee,
                "club": club_amount,
            },
        }

    # Academy cohort enrollment
    elif payload.purpose == PaymentPurpose.ACADEMY_COHORT:
        if not payload.enrollment_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="enrollment_id is required for ACADEMY_COHORT payments",
            )
        # Lookup enrollment and cohort price from academy_service
        headers = {"Authorization": f"Bearer {_service_role_jwt()}"}
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{settings.ACADEMY_SERVICE_URL}/enrollments/{payload.enrollment_id}",
                headers=headers,
            )
            if resp.status_code >= 400:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Failed to fetch enrollment: {resp.text}",
                )
            enrollment_data = resp.json()
            cohort_id = enrollment_data.get("cohort_id")
            program = enrollment_data.get("program") or {}
            amount = float(program.get("price") or 0)

        if amount == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cohort/Program has no price set",
            )

        payment_metadata = {
            **(payload.payment_metadata or {}),
            "enrollment_id": str(payload.enrollment_id),
            "cohort_id": str(cohort_id) if cohort_id else None,
        }

    else:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=f"Payment intent not implemented for purpose={payload.purpose}",
        )

    # Apply discount if provided
    original_amount = amount
    discount_applied = None
    discount_code_used = None

    if payload.discount_code:
        amount, discount_applied, discount_obj = await _validate_and_apply_discount(
            db=db,
            discount_code=payload.discount_code,
            purpose=payload.purpose,
            original_amount=original_amount,
            member_auth_id=current_user.user_id,
        )
        if discount_obj:
            discount_code_used = discount_obj.code
            payment_metadata = {
                **payment_metadata,
                "discount_code": discount_obj.code,
                "discount_type": discount_obj.discount_type.value,
                "discount_value": discount_obj.value,
                "discount_applied": discount_applied,
                "original_amount": original_amount,
            }

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

    # Save pending payment reference to member for cross-device resumption
    await _update_pending_payment_reference(current_user.user_id, payment.reference)

    # Build extension info for response (only for CLUB payments)
    response_extension_info = {}
    if payload.purpose == PaymentPurpose.CLUB:
        response_extension_info = {
            "requires_community_extension": requires_community_extension,
            "community_extension_months": community_extension_months,
            "community_extension_amount": community_extension_amount,
            "total_with_extension": payment.amount + community_extension_amount if not payload.include_community_extension else None,
        }

    return PaymentIntentResponse(
        reference=payment.reference,
        amount=payment.amount,
        currency=payment.currency,
        purpose=payment.purpose,
        status=payment.status,
        checkout_url=checkout_url,
        created_at=payment.created_at,
        original_amount=original_amount if discount_applied else None,
        discount_applied=discount_applied,
        discount_code=discount_code_used,
        **response_extension_info,
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


# --- Admin Discount Endpoints ---


@router.post("/admin/discounts", response_model=DiscountResponse)
async def create_discount(
    payload: DiscountCreate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Create a new discount code (Admin only)."""
    from services.payments_service.models import DiscountType as DT

    # Check if code already exists
    existing = await db.execute(
        select(Discount).where(Discount.code == payload.code.upper().strip())
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Discount code '{payload.code}' already exists",
        )

    discount = Discount(
        code=payload.code.upper().strip(),
        description=payload.description,
        discount_type=DT(payload.discount_type),
        value=payload.value,
        applies_to=payload.applies_to,
        valid_from=payload.valid_from,
        valid_until=payload.valid_until,
        max_uses=payload.max_uses,
        max_uses_per_user=payload.max_uses_per_user,
        is_active=payload.is_active,
    )
    db.add(discount)
    await db.commit()
    await db.refresh(discount)
    return discount


@router.get("/admin/discounts", response_model=list[DiscountResponse])
async def list_discounts(
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """List all discount codes (Admin only)."""
    result = await db.execute(select(Discount).order_by(desc(Discount.created_at)))
    return result.scalars().all()


@router.get("/admin/discounts/{discount_id}", response_model=DiscountResponse)
async def get_discount(
    discount_id: str,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Get a specific discount code (Admin only)."""
    import uuid as uuid_mod

    try:
        uid = uuid_mod.UUID(discount_id)
        result = await db.execute(select(Discount).where(Discount.id == uid))
    except ValueError:
        # Try by code
        result = await db.execute(
            select(Discount).where(Discount.code == discount_id.upper().strip())
        )

    discount = result.scalar_one_or_none()
    if not discount:
        raise HTTPException(status_code=404, detail="Discount not found")
    return discount


@router.patch("/admin/discounts/{discount_id}", response_model=DiscountResponse)
async def update_discount(
    discount_id: str,
    payload: DiscountUpdate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Update a discount code (Admin only)."""
    import uuid as uuid_mod
    from services.payments_service.models import DiscountType as DT

    try:
        uid = uuid_mod.UUID(discount_id)
        result = await db.execute(select(Discount).where(Discount.id == uid))
    except ValueError:
        result = await db.execute(
            select(Discount).where(Discount.code == discount_id.upper().strip())
        )

    discount = result.scalar_one_or_none()
    if not discount:
        raise HTTPException(status_code=404, detail="Discount not found")

    update_data = payload.model_dump(exclude_unset=True)
    if "discount_type" in update_data and update_data["discount_type"]:
        update_data["discount_type"] = DT(update_data["discount_type"])

    for field, value in update_data.items():
        setattr(discount, field, value)

    await db.commit()
    await db.refresh(discount)
    return discount


@router.delete("/admin/discounts/{discount_id}")
async def delete_discount(
    discount_id: str,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Delete a discount code (Admin only)."""
    import uuid as uuid_mod

    try:
        uid = uuid_mod.UUID(discount_id)
        result = await db.execute(select(Discount).where(Discount.id == uid))
    except ValueError:
        result = await db.execute(
            select(Discount).where(Discount.code == discount_id.upper().strip())
        )

    discount = result.scalar_one_or_none()
    if not discount:
        raise HTTPException(status_code=404, detail="Discount not found")

    await db.delete(discount)
    await db.commit()
    return {"deleted": True, "code": discount.code}
