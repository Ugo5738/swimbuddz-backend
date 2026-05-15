"""POST /payments/intents — kick off a payment for a member.

Branches on `PaymentPurpose` to compute the correct amount (and any
community-extension top-ups for Club purchases), applies discounts +
Bubbles, persists a PENDING Payment row, and (for the paystack
method) initializes the Paystack checkout and returns the
authorization URL.
"""

import hashlib
import hmac
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import _service_role_jwt, get_current_user, require_admin
from libs.auth.models import AuthUser
from libs.common.config import get_settings
from libs.common.currency import KOBO_PER_NAIRA
from libs.common.emails.client import get_email_client
from libs.common.logging import get_logger
from libs.common.service_client import (
    dispatch_notification,
    emit_rewards_event,
    get_member_by_auth_id,
    internal_post,
)
from libs.common.datetime_utils import utc_now
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
    PaymentIntentResponse,
    PaymentResponse,
    PricingConfigResponse,
    SessionAttendanceRole,
    SessionAttendanceStatus,
)

settings = get_settings()
logger = get_logger(__name__)

FULFILLMENT_META_KEY = "fulfillment"
MAX_FULFILLMENT_RETRIES = 8
BASE_FULFILLMENT_RETRY_MINUTES = 2

from ._discounts import _validate_and_apply_discount
from ._entitlement import _mark_paid_and_apply
from ._helpers import (
    _require_attendance_status,
    _resolve_club_amount,
    _update_pending_payment_reference,
)
from ._paystack import _initialize_paystack, _paystack_enabled

router = APIRouter()


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
            headers = {"Authorization": f"Bearer {_service_role_jwt('payments')}"}
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
                        from dateutil.relativedelta import relativedelta

                        community_until = datetime.fromisoformat(
                            community_until_str.replace("Z", "+00:00")
                        )
                        club_end = utc_now() + relativedelta(months=months)

                        if club_end > community_until:
                            # Calculate months needed to extend Community
                            diff_days = (club_end - community_until).days
                            community_extension_months = max(
                                1, (diff_days + 29) // 30
                            )  # Round up
                            community_monthly_rate = (
                                getattr(settings, "COMMUNITY_ANNUAL_FEE_NGN", 20000)
                                / 12
                            )
                            community_extension_amount = round(
                                community_monthly_rate * community_extension_months, 2
                            )
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
            "community_extension_months": (
                community_extension_months if payload.include_community_extension else 0
            ),
            "community_extension_amount": (
                community_extension_amount if payload.include_community_extension else 0
            ),
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
        # Lookup enrollment and next payable installment from academy_service.
        # Pass use_installments so the academy service can build the schedule on-demand
        # if the member opted in and no schedule exists yet.
        headers = {"Authorization": f"Bearer {_service_role_jwt('payments')}"}
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{settings.ACADEMY_SERVICE_URL}/internal/academy/enrollments/{payload.enrollment_id}",
                params={"use_installments": str(payload.use_installments).lower()},
                headers=headers,
            )
            if resp.status_code >= 400:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Failed to fetch enrollment: {resp.text}",
                )
            enrollment_data = resp.json()
            cohort_id = enrollment_data.get("cohort_id")
            installments = sorted(
                enrollment_data.get("installments") or [],
                key=lambda i: i.get("installment_number", 0),
            )

        paid_statuses = {"paid", "waived"}
        next_installment = next(
            (
                i
                for i in installments
                if str(i.get("status") or "").lower() not in paid_statuses
            ),
            None,
        )

        if next_installment:
            # Academy returns installment amounts in kobo; convert to NGN for payment intent.
            amount = float(next_installment.get("amount") or 0) / KOBO_PER_NAIRA
        else:
            # Backward-compatible fallback for older enrollments without an installment plan.
            program = enrollment_data.get("program") or {}
            cohort = enrollment_data.get("cohort") or {}
            amount = float(
                cohort.get("price_override")
                if cohort.get("price_override") is not None
                else (program.get("price_amount") or 0)
            )
            if str(enrollment_data.get("payment_status") or "").lower() == "paid":
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="All required academy installments are already paid",
                )

        # Member-initiated custom amount: must be >= next installment amount
        # and <= remaining balance (founder policy May 2026). Default behavior
        # without an override is unchanged — charge exactly the stipulated amount.
        if (
            payload.amount_override_kobo is not None
            and payload.amount_override_kobo > 0
        ):
            override_naira = payload.amount_override_kobo / KOBO_PER_NAIRA
            if override_naira < amount:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"Custom amount NGN {override_naira:,.2f} is less than the "
                        f"next stipulated installment NGN {amount:,.2f}"
                    ),
                )
            remaining_balance_kobo = sum(
                int(i.get("amount") or 0)
                for i in installments
                if str(i.get("status") or "").lower() not in paid_statuses
            )
            remaining_balance_naira = remaining_balance_kobo / KOBO_PER_NAIRA
            if override_naira > remaining_balance_naira:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"Custom amount NGN {override_naira:,.2f} exceeds remaining "
                        f"balance NGN {remaining_balance_naira:,.2f}"
                    ),
                )
            amount = override_naira

        if amount <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No payable installment is available for this enrollment",
            )

        payment_metadata = {
            **(payload.payment_metadata or {}),
            "enrollment_id": str(payload.enrollment_id),
            "cohort_id": str(cohort_id) if cohort_id else None,
            "installment_id": (
                str(next_installment.get("id")) if next_installment else None
            ),
            "installment_number": (
                int(next_installment.get("installment_number"))
                if next_installment and next_installment.get("installment_number")
                else None
            ),
            "installment_due_at": (
                next_installment.get("due_at") if next_installment else None
            ),
            "total_installments": (
                int(enrollment_data.get("total_installments") or 0) or None
            ),
        }

    # Store order payment
    elif payload.purpose == PaymentPurpose.STORE_ORDER:
        if not payload.order_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="order_id is required for STORE_ORDER payments",
            )
        # Lookup order and total from store_service
        headers = {"Authorization": f"Bearer {_service_role_jwt('payments')}"}
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{settings.STORE_SERVICE_URL}/store/admin/orders/{payload.order_id}",
                headers=headers,
            )
            if resp.status_code >= 400:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Failed to fetch order: {resp.text}",
                )
            order_data = resp.json()
            amount = float(order_data.get("total_ngn") or 0)

        if amount <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Order total must be greater than zero",
            )

        payment_metadata = {
            **(payload.payment_metadata or {}),
            "order_id": str(payload.order_id),
            "order_number": order_data.get("order_number"),
        }

    # Session fee payment (pool fee + ride share)
    elif payload.purpose == PaymentPurpose.SESSION_FEE:
        if not payload.session_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="session_id is required for SESSION_FEE payments",
            )
        if not payload.direct_amount or payload.direct_amount <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="direct_amount is required and must be greater than zero for SESSION_FEE payments",
            )
        amount = float(payload.direct_amount)
        attendance_status = _require_attendance_status(
            payload.attendance_status,
            source="attendance_status",
        )
        payment_metadata = {
            **(payload.payment_metadata or {}),
            "session_id": str(payload.session_id),
            "ride_config_id": (
                str(payload.ride_config_id) if payload.ride_config_id else None
            ),
            "pickup_location_id": (
                str(payload.pickup_location_id) if payload.pickup_location_id else None
            ),
            "attendance_status": attendance_status.value,
            "num_seats": payload.num_seats or 1,
            "bubbles_to_apply": payload.bubbles_to_apply or 0,
        }

    # Session bundle — book multiple sessions in one payment intent
    elif payload.purpose == PaymentPurpose.SESSION_BUNDLE:
        if not payload.session_ids or len(payload.session_ids) == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="session_ids is required for SESSION_BUNDLE payments",
            )
        if len(payload.session_ids) > 10:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Maximum 10 sessions per bundle",
            )
        # Check for duplicates
        unique_ids = list({str(sid) for sid in payload.session_ids})
        if len(unique_ids) != len(payload.session_ids):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Duplicate session_ids in bundle",
            )
        if not payload.direct_amount or payload.direct_amount <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="direct_amount is required and must be greater than zero for SESSION_BUNDLE payments",
            )
        amount = float(payload.direct_amount)
        # Validate per-session ride configs (if provided) — every key must be
        # one of the session_ids in the bundle.
        ride_configs_meta: dict = {}
        if payload.session_ride_configs:
            bundle_id_set = {str(sid) for sid in payload.session_ids}
            for sid_key, ride_cfg in payload.session_ride_configs.items():
                if str(sid_key) not in bundle_id_set:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"session_ride_configs key {sid_key} is not in session_ids",
                    )
                ride_configs_meta[str(sid_key)] = {
                    "ride_config_id": str(ride_cfg.ride_config_id),
                    "pickup_location_id": str(ride_cfg.pickup_location_id),
                    "num_seats": int(ride_cfg.num_seats),
                }
        payment_metadata = {
            **(payload.payment_metadata or {}),
            "session_ids": [str(sid) for sid in payload.session_ids],
            "session_count": len(payload.session_ids),
            "session_ride_configs": ride_configs_meta if ride_configs_meta else None,
        }

    # Standalone ride share payment (after session already booked)
    elif payload.purpose == PaymentPurpose.RIDE_SHARE:
        if not payload.session_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="session_id is required for RIDE_SHARE payments",
            )
        if not payload.ride_config_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="ride_config_id is required for RIDE_SHARE payments",
            )
        if not payload.pickup_location_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="pickup_location_id is required for RIDE_SHARE payments",
            )
        if not payload.direct_amount or payload.direct_amount <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="direct_amount is required and must be greater than zero for RIDE_SHARE payments",
            )
        amount = float(payload.direct_amount)
        num_seats = payload.num_seats or 1
        payment_metadata = {
            **(payload.payment_metadata or {}),
            "session_id": str(payload.session_id),
            "ride_config_id": str(payload.ride_config_id),
            "pickup_location_id": str(payload.pickup_location_id),
            "num_seats": num_seats,
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
        # Get components for smart discount matching (CLUB_BUNDLE has components in metadata)
        discount_components = (
            payment_metadata.get("components")
            if payload.purpose == PaymentPurpose.CLUB_BUNDLE
            else None
        )

        (
            amount,
            discount_applied,
            discount_obj,
            applies_to_component,
        ) = await _validate_and_apply_discount(
            db=db,
            discount_code=payload.discount_code,
            purpose=payload.purpose,
            original_amount=original_amount,
            member_auth_id=current_user.user_id,
            components=discount_components,
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
                "discount_applies_to_component": applies_to_component,
            }

    # Partial Bubbles: subtract the Bubbles value from amount AFTER discount.
    # 1 Bubble = ₦100. Only applies to SESSION_FEE / SESSION_BUNDLE / RIDE_SHARE.
    bubbles_purposes = {
        PaymentPurpose.SESSION_FEE,
        PaymentPurpose.SESSION_BUNDLE,
        PaymentPurpose.RIDE_SHARE,
    }
    bubbles_to_apply_val = payload.bubbles_to_apply or 0
    if bubbles_to_apply_val > 0 and payload.purpose in bubbles_purposes:
        bubbles_value_ngn = bubbles_to_apply_val * 100
        if bubbles_value_ngn > amount:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="bubbles_to_apply exceeds amount after discount",
            )
        amount = amount - bubbles_value_ngn
        payment_metadata = {
            **payment_metadata,
            "bubbles_to_apply": bubbles_to_apply_val,
            "bubbles_value_ngn": bubbles_value_ngn,
        }

    payment = Payment(
        reference=Payment.generate_reference(),
        member_auth_id=current_user.user_id,
        payer_email=current_user.email,
        purpose=payload.purpose,
        amount=amount,
        currency=payload.currency,
        status=PaymentStatus.PENDING,
        payment_method=payload.payment_method,  # paystack or manual_transfer
        payment_metadata=payment_metadata,
    )

    db.add(payment)
    await db.commit()
    await db.refresh(payment)

    checkout_url = None

    # Paystack (and most payment providers) cannot initialize a transaction for 0 NGN.
    # If a discount brings the payable amount to 0, complete the payment internally and
    # apply the entitlement immediately.
    if payment.amount <= 0:
        if not payload.discount_code:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Amount must be greater than zero",
            )
        discount_ref = (discount_code_used or payload.discount_code).upper().strip()
        payment = await _mark_paid_and_apply(
            db=db,
            payment=payment,
            provider="discount",
            provider_reference=f"discount:{discount_ref}",
            paid_at=utc_now(),
            provider_payload={
                "discount_code": discount_code_used or payload.discount_code,
                "discount_applied": discount_applied,
                "original_amount": original_amount,
            },
        )

    # Only initialize Paystack for online payments
    if (
        payment.status == PaymentStatus.PENDING
        and payload.payment_method == "paystack"
        and _paystack_enabled()
    ):
        if not current_user.email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Authenticated user email is required to initialize Paystack",
            )
        # Determine redirect path based on purpose
        redirect_path = None
        if payload.purpose == PaymentPurpose.ACADEMY_COHORT and payload.enrollment_id:
            redirect_path = f"/account/academy/enrollment-success?enrollment_id={payload.enrollment_id}"

        authorization_url, access_code = await _initialize_paystack(
            payment, current_user.email, redirect_path
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
    if payment.status == PaymentStatus.PENDING:
        await _update_pending_payment_reference(current_user.user_id, payment.reference)

    # Build extension info for response (only for CLUB payments)
    response_extension_info = {}
    if payload.purpose == PaymentPurpose.CLUB:
        response_extension_info = {
            "requires_community_extension": requires_community_extension,
            "community_extension_months": community_extension_months,
            "community_extension_amount": community_extension_amount,
            "total_with_extension": (
                payment.amount + community_extension_amount
                if not payload.include_community_extension
                else None
            ),
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
