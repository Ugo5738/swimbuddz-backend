import hashlib
import hmac
import json
import uuid
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from libs.auth.dependencies import (
    _service_role_jwt,
    get_current_user,
    require_admin,
    require_service_role,
)
from libs.auth.models import AuthUser
from libs.common.config import get_settings
from libs.common.currency import KOBO_PER_NAIRA
from libs.common.emails.client import get_email_client
from libs.common.logging import get_logger
from libs.common.service_client import internal_post
from libs.db.session import get_async_db
from pydantic import BaseModel
from services.payments_service.models import (
    CoachPayout,
    Discount,
    DiscountType,
    Payment,
    PaymentPurpose,
    PaymentStatus,
    PayoutStatus,
)
from services.payments_service.schemas import (
    ClubBillingCycle,
    CompletePaymentRequest,
    CreatePaymentIntentRequest,
    DiscountCreate,
    DiscountResponse,
    DiscountUpdate,
    InternalInitializeRequest,
    InternalInitializeResponse,
    InternalPaystackVerifyResponse,
    PaymentIntentResponse,
    PaymentResponse,
    PricingConfigResponse,
    SessionAttendanceRole,
    SessionAttendanceStatus,
)
from sqlalchemy import delete, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/payments", tags=["payments"])
settings = get_settings()
logger = get_logger(__name__)

FULFILLMENT_META_KEY = "fulfillment"
MAX_FULFILLMENT_RETRIES = 8
BASE_FULFILLMENT_RETRY_MINUTES = 2


@router.get("/pricing", response_model=PricingConfigResponse)
async def get_pricing_config():
    """
    Get public pricing configuration for membership tiers.
    No authentication required - used by frontend to display prices.
    """
    return PricingConfigResponse(
        community_annual=settings.COMMUNITY_ANNUAL_FEE_NGN,
        club_quarterly=settings.CLUB_QUARTERLY_FEE_NGN,
        club_biannual=settings.CLUB_BIANNUAL_FEE_NGN,
        club_annual=settings.CLUB_ANNUAL_FEE_NGN,
        currency="NGN",
    )


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
    return int(value * KOBO_PER_NAIRA)


def _verify_paystack_signature(raw_body: bytes, signature: str) -> bool:
    secret = (settings.PAYSTACK_SECRET_KEY or "").encode("utf-8")
    digest = hmac.new(secret, raw_body, hashlib.sha512).hexdigest()
    return hmac.compare_digest(digest, signature)


def _callback_url(reference: str, redirect_path: str = None) -> str:
    # If a service provides a redirect path (e.g. wallet topup), always honor it.
    # PAYSTACK_CALLBACK_URL is treated as default only when no redirect is provided.
    if redirect_path:
        if redirect_path.startswith(("http://", "https://")):
            callback = redirect_path
        else:
            base = settings.FRONTEND_URL.rstrip("/")
            path = (
                redirect_path if redirect_path.startswith("/") else f"/{redirect_path}"
            )
            callback = f"{base}{path}"
    elif settings.PAYSTACK_CALLBACK_URL:
        callback = settings.PAYSTACK_CALLBACK_URL
    else:
        base = settings.FRONTEND_URL.rstrip("/")
        callback = f"{base}/account/billing"

    # Ensure provider marker is present for frontend return handling.
    parts = urlsplit(callback)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.setdefault("provider", "paystack")
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
    )


def _require_attendance_status(
    raw_status: SessionAttendanceStatus | str | None, source: str
) -> SessionAttendanceStatus:
    if isinstance(raw_status, SessionAttendanceStatus):
        return raw_status
    status_value = str(raw_status or "").strip()
    if not status_value:
        return SessionAttendanceStatus.PRESENT
    try:
        return SessionAttendanceStatus(status_value)
    except ValueError:
        allowed = ", ".join([status.value for status in SessionAttendanceStatus])
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{source} must be one of: {allowed}",
        )


async def _update_pending_payment_reference(
    auth_id: str, reference: str | None
) -> None:
    """Update or clear the pending_payment_reference on a member's membership."""
    headers = {"Authorization": f"Bearer {_service_role_jwt('payments')}"}
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.patch(
            f"{settings.MEMBERS_SERVICE_URL}/admin/members/by-auth/{auth_id}/membership",
            json={"pending_payment_reference": reference},
            headers=headers,
        )
        # Ignore failures - this is a best-effort feature
        if resp.status_code >= 400:
            logger.warning(
                f"Failed to update pending_payment_reference for {auth_id}: {resp.status_code}"
            )


async def _initialize_paystack(
    payment: Payment, email: str, redirect_path: str = None
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
        "callback_url": _callback_url(payment.reference, redirect_path),
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


def _next_retry_time(attempts: int) -> datetime:
    # Exponential backoff capped at 60 minutes.
    delay = min(60, BASE_FULFILLMENT_RETRY_MINUTES * (2 ** max(attempts - 1, 0)))
    return datetime.now(timezone.utc) + timedelta(minutes=delay)


def _fulfillment_meta(payment: Payment) -> dict:
    metadata = payment.payment_metadata or {}
    return dict(metadata.get(FULFILLMENT_META_KEY) or {})


def _set_fulfillment_meta(payment: Payment, **fields) -> None:
    metadata = dict(payment.payment_metadata or {})
    fulfillment = _fulfillment_meta(payment)
    fulfillment.update(fields)
    metadata[FULFILLMENT_META_KEY] = fulfillment
    payment.payment_metadata = metadata


async def _apply_entitlement_with_tracking(payment: Payment) -> None:
    now = datetime.now(timezone.utc)
    existing = _fulfillment_meta(payment)
    attempts = int(existing.get("attempts") or 0) + 1

    _set_fulfillment_meta(
        payment,
        status="in_progress",
        attempts=attempts,
        last_attempt_at=now.isoformat(),
    )

    try:
        await _apply_entitlement(payment)
        payment.entitlement_applied_at = now
        payment.entitlement_error = None
        _set_fulfillment_meta(
            payment,
            status="applied",
            next_retry_at=None,
            last_error=None,
        )
    except Exception as exc:
        error_message = str(exc)
        payment.entitlement_error = error_message

        if attempts >= MAX_FULFILLMENT_RETRIES:
            _set_fulfillment_meta(
                payment,
                status="dead_letter",
                next_retry_at=None,
                last_error=error_message,
            )
        else:
            retry_at = _next_retry_time(attempts)
            _set_fulfillment_meta(
                payment,
                status="retry_scheduled",
                next_retry_at=retry_at.isoformat(),
                last_error=error_message,
            )

        logger.warning(
            "Entitlement apply failed for %s (attempt %d/%d): %s",
            payment.reference,
            attempts,
            MAX_FULFILLMENT_RETRIES,
            error_message,
        )


async def _apply_entitlement(payment: Payment) -> None:
    # Handle Community activation
    if payment.purpose == PaymentPurpose.COMMUNITY:
        path = f"/admin/members/by-auth/{payment.member_auth_id}/community/activate"
        years = int((payment.payment_metadata or {}).get("years") or 1)
        payload = {"years": years}

    # Handle Club add-on (may include community extension)
    elif payment.purpose == PaymentPurpose.CLUB:
        months = int((payment.payment_metadata or {}).get("months") or 1)
        community_extension_months = int(
            (payment.payment_metadata or {}).get("community_extension_months") or 0
        )

        headers = {"Authorization": f"Bearer {_service_role_jwt('payments')}"}
        async with httpx.AsyncClient(timeout=30) as client:
            # If community extension was included, extend Community first
            if community_extension_months > 0:
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
        headers = {"Authorization": f"Bearer {_service_role_jwt('payments')}"}
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
        installment_id = (payment.payment_metadata or {}).get("installment_id")
        installment_number = (payment.payment_metadata or {}).get("installment_number")
        total_installments = (payment.payment_metadata or {}).get("total_installments")
        headers = {"Authorization": f"Bearer {_service_role_jwt('payments')}"}
        mark_paid_payload = {
            "payment_reference": payment.reference,
            "paid_at": (
                payment.paid_at.isoformat()
                if payment.paid_at
                else datetime.now(timezone.utc).isoformat()
            ),
        }
        if payment.amount <= 0:
            # Fully discounted enrollment should not retain installment obligations.
            mark_paid_payload["clear_installments"] = True
        if installment_id:
            mark_paid_payload["installment_id"] = installment_id
        if installment_number:
            mark_paid_payload["installment_number"] = installment_number
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{settings.ACADEMY_SERVICE_URL}/academy/admin/enrollments/{enrollment_id}/mark-paid",
                headers=headers,
                json=mark_paid_payload,
            )
            if resp.status_code >= 400:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"Failed to mark enrollment as paid ({resp.status_code}): {resp.text}",
                )

            # Activate the academy tier on the member using the cohort end_date from
            # the mark-paid response. We always pass the latest cohort end date so
            # members with multiple simultaneous enrollments keep access until their
            # last cohort finishes (the activate endpoint keeps the later date).
            try:
                enrollment_data = resp.json()
                cohort_end_date = (enrollment_data.get("cohort") or {}).get(
                    "end_date"
                ) or enrollment_data.get("cohort_end_date")
                if cohort_end_date and payment.member_auth_id:
                    academy_resp = await client.post(
                        f"{settings.MEMBERS_SERVICE_URL}/admin/members/by-auth/{payment.member_auth_id}/academy/activate",
                        headers=headers,
                        json={"cohort_end_date": cohort_end_date},
                    )
                    if academy_resp.status_code >= 400:
                        logger.warning(
                            f"Failed to activate academy tier for {payment.member_auth_id}: "
                            f"{academy_resp.status_code} {academy_resp.text}"
                        )
                else:
                    logger.warning(
                        f"Academy cohort end_date missing in mark-paid response for "
                        f"enrollment {enrollment_id} — academy tier not activated"
                    )
            except Exception as e:
                # Non-fatal: enrollment is paid; tier activation failure is logged
                logger.error(
                    f"Failed to activate academy tier after payment {payment.reference}: {e}"
                )

        # Clear pending payment reference on success
        await _update_pending_payment_reference(payment.member_auth_id, None)

        # Send subsequent installment payment confirmation (not for first installment —
        # first installment confirmation is sent by the academy service's mark-paid endpoint).
        if installment_number and int(installment_number) > 1:
            try:
                member_headers = {
                    "Authorization": f"Bearer {_service_role_jwt('payments')}"
                }
                async with httpx.AsyncClient(timeout=30) as client:
                    member_resp = await client.get(
                        f"{settings.MEMBERS_SERVICE_URL}/members/by-auth/{payment.member_auth_id}",
                        headers=member_headers,
                    )
                    if member_resp.status_code < 400:
                        member_data = member_resp.json()
                        member_email = member_data.get("email") or payment.payer_email
                        member_name = member_data.get("first_name", "Student")
                    else:
                        member_email = payment.payer_email
                        member_name = "Student"

                if member_email:
                    email_client = get_email_client()
                    await email_client.send_template(
                        template_type="installment_payment_confirmation",
                        to_email=member_email,
                        template_data={
                            "member_name": member_name,
                            "installment_number": int(installment_number),
                            "total_installments": (
                                int(total_installments) if total_installments else None
                            ),
                            "amount": payment.amount,
                            "currency": payment.currency,
                            "payment_reference": payment.reference,
                            "paid_at": (
                                payment.paid_at.strftime("%B %d, %Y")
                                if payment.paid_at
                                else datetime.now(timezone.utc).strftime("%B %d, %Y")
                            ),
                        },
                    )
                    logger.info(
                        f"Sent installment payment confirmation to {member_email} "
                        f"(installment {installment_number} of {total_installments})"
                    )
            except Exception as e:
                # Non-fatal — payment was successful; email failure must not raise
                logger.error(
                    f"Failed to send installment payment confirmation for {payment.reference}: {e}"
                )
        return

    # Handle Store order payment
    elif payment.purpose == PaymentPurpose.STORE_ORDER:
        order_id = (payment.payment_metadata or {}).get("order_id")
        if not order_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="order_id missing in payment metadata",
            )
        headers = {"Authorization": f"Bearer {_service_role_jwt('payments')}"}
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{settings.STORE_SERVICE_URL}/store/admin/orders/{order_id}/mark-paid",
                headers=headers,
            )
            if resp.status_code >= 400:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"Failed to mark store order as paid ({resp.status_code}): {resp.text}",
                )
        # No pending_payment_reference to clear for store orders
        return

    # Handle wallet topup payment
    elif payment.purpose == PaymentPurpose.WALLET_TOPUP:
        topup_reference = (payment.payment_metadata or {}).get(
            "topup_reference"
        ) or payment.reference
        resp = await internal_post(
            service_url=settings.WALLET_SERVICE_URL,
            path="/internal/wallet/confirm-topup",
            calling_service="payments",
            json={
                "topup_reference": topup_reference,
                "payment_reference": payment.reference,
                "status": "completed",
            },
            timeout=30.0,
        )
        if resp.status_code >= 400:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to confirm wallet topup ({resp.status_code}): {resp.text}",
            )
        return

    # Handle Session fee payment - create attendance and optionally ride booking
    elif payment.purpose == PaymentPurpose.SESSION_FEE:
        session_id = (payment.payment_metadata or {}).get("session_id")
        ride_config_id = (payment.payment_metadata or {}).get("ride_config_id")
        pickup_location_id = (payment.payment_metadata or {}).get("pickup_location_id")
        attendance_status = _require_attendance_status(
            (payment.payment_metadata or {}).get(
                "attendance_status", SessionAttendanceStatus.PRESENT.value
            ),
            source="payment_metadata.attendance_status",
        )

        if not session_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="session_id missing in payment metadata",
            )

        headers = {"Authorization": f"Bearer {_service_role_jwt('payments')}"}
        async with httpx.AsyncClient(timeout=30) as client:
            # First, look up the member_id from auth_id via members service
            member_resp = await client.get(
                f"{settings.MEMBERS_SERVICE_URL}/members/by-auth/{payment.member_auth_id}",
                headers=headers,
            )
            if member_resp.status_code >= 400:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"Failed to look up member ({member_resp.status_code}): {member_resp.text}",
                )
            member_data = member_resp.json()
            member_id = member_data.get("id")

            # Create attendance record via attendance service
            attendance_resp = await client.post(
                f"{settings.ATTENDANCE_SERVICE_URL}/attendance/sessions/{session_id}/attendance/public",
                json={
                    "member_id": member_id,
                    "status": attendance_status.value,
                    "role": SessionAttendanceRole.SWIMMER.value,
                    "notes": f"Payment ref: {payment.reference}",
                },
                headers=headers,
            )
            if attendance_resp.status_code >= 400:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"Failed to create attendance ({attendance_resp.status_code}): {attendance_resp.text}",
                )

            # If ride share was selected, create the booking via transport service
            if ride_config_id and pickup_location_id:
                transport_resp = await client.post(
                    f"{settings.TRANSPORT_SERVICE_URL}/transport/sessions/{session_id}/bookings",
                    json={
                        "session_ride_config_id": ride_config_id,
                        "pickup_location_id": pickup_location_id,
                    },
                    params={"member_id": member_id},
                    headers=headers,
                )
                # Log but don't fail if ride booking fails
                if transport_resp.status_code >= 400:
                    logger.warning(f"Ride booking failed: {transport_resp.text}")

            # Send session confirmation email
            try:
                # Fetch session details for email
                session_resp = await client.get(
                    f"{settings.SESSIONS_SERVICE_URL}/sessions/{session_id}",
                    headers=headers,
                )
                session_data = {}
                if session_resp.status_code < 400:
                    session_data = session_resp.json()

                # Get member email and name
                member_email = member_data.get("email", "")
                member_name = f"{member_data.get('first_name', '')} {member_data.get('last_name', '')}".strip()

                # Parse session times
                starts_at = session_data.get("starts_at", "")
                session_date = ""
                session_time = ""
                if starts_at:
                    try:
                        dt = datetime.fromisoformat(starts_at.replace("Z", "+00:00"))
                        session_date = dt.strftime("%A, %B %d, %Y")
                        session_time = f"{dt.strftime('%I:%M %p')} - "
                        ends_at = session_data.get("ends_at", "")
                        if ends_at:
                            end_dt = datetime.fromisoformat(
                                ends_at.replace("Z", "+00:00")
                            )
                            session_time += end_dt.strftime("%I:%M %p")
                    except Exception:
                        session_date = (
                            starts_at[:10] if len(starts_at) >= 10 else starts_at
                        )

                # Get ride share details if booked
                ride_share_area = None
                pickup_name = None
                pickup_description = None
                departure_time = None
                ride_distance = None
                ride_duration = None
                if ride_config_id and pickup_location_id:
                    ride_areas = session_data.get(
                        "rideShareAreas", []
                    ) or session_data.get("ride_share_areas", [])
                    for area in ride_areas:
                        if area.get("id") == ride_config_id:
                            ride_share_area = area.get(
                                "ride_area_name", ""
                            ) or area.get("area_name", "")
                            ride_distance = area.get("distance_km", "")
                            ride_duration = area.get("duration_minutes", "")
                            if ride_distance:
                                ride_distance = f"{ride_distance} km"
                            if ride_duration:
                                ride_duration = f"{ride_duration} min"

                            pickup_locs = area.get("pickup_locations", [])
                            for loc in pickup_locs:
                                if loc.get("id") == pickup_location_id:
                                    pickup_name = loc.get("name", "")
                                    pickup_description = loc.get(
                                        "description", ""
                                    ) or loc.get("address", "")
                                    departure_time = loc.get(
                                        "departure_time_calculated", ""
                                    ) or loc.get("departure_time", "")
                                    break
                            break

                # Send the email via centralized email service
                if member_email:
                    email_client = get_email_client()
                    await email_client.send_template(
                        template_type="session_confirmation",
                        to_email=member_email,
                        template_data={
                            "member_name": member_name or "Member",
                            "member_id": member_id,
                            "session_title": session_data.get(
                                "title", "Swimming Session"
                            ),
                            "session_date": session_date,
                            "session_time": session_time,
                            "session_location": session_data.get("location_name", "")
                            or session_data.get("location", ""),
                            "session_address": session_data.get("address", ""),
                            "amount_paid": float(payment.amount),
                            "ride_share_area": ride_share_area,
                            "pickup_location": pickup_name,
                            "pickup_description": pickup_description,
                            "departure_time": departure_time,
                            "ride_distance": ride_distance,
                            "ride_duration": ride_duration,
                            "currency": payment.currency,
                        },
                    )
                    logger.info(f"Session confirmation email sent to {member_email}")
            except Exception as e:
                # Log but don't fail if email fails
                logger.error(f"Failed to send session confirmation email: {e}")

        # Clear pending payment reference on success
        await _update_pending_payment_reference(payment.member_auth_id, None)
        return

    else:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=f"Entitlement application not implemented for purpose={payment.purpose}",
        )

    headers = {"Authorization": f"Bearer {_service_role_jwt('payments')}"}
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
    components: (
        dict[str, float] | None
    ) = None,  # e.g., {"community": 20000, "club": 150000}
) -> tuple[float, float | None, Discount | None, str | None]:
    """
    Validate and apply a discount code if provided.
    Returns: (final_amount, discount_applied, discount_obj, applies_to_component)

    Smart Component Matching:
    - If payment is CLUB_BUNDLE and discount only applies to COMMUNITY,
      discount is calculated on the COMMUNITY portion only.
    """
    if not discount_code:
        return original_amount, None, None, None

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

    # Smart Component Matching
    applicable_purposes = [p.upper() for p in (discount.applies_to or [])]
    purpose_upper = purpose.value.upper()

    # Determine what amount the discount applies to
    applicable_amount = original_amount
    applies_to_component = None

    if applicable_purposes:
        # Direct match - discount applies to the exact purpose
        if purpose_upper in applicable_purposes:
            applicable_amount = original_amount
            applies_to_component = purpose_upper.lower()

        # Smart component matching for bundles
        elif purpose_upper == "CLUB_BUNDLE" and components:
            # Check if discount applies to COMMUNITY portion
            if "COMMUNITY" in applicable_purposes and "community" in components:
                applicable_amount = components["community"]
                applies_to_component = "community"
            # Check if discount applies to CLUB portion
            elif "CLUB" in applicable_purposes and "club" in components:
                applicable_amount = components["club"]
                applies_to_component = "club"
            else:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Discount code does not apply to any component in this payment",
                )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Discount code does not apply to {purpose.value} payments",
            )

    # Calculate discount amount based on applicable amount
    if discount.discount_type == DiscountType.PERCENTAGE:
        discount_amount = applicable_amount * (discount.value / 100)
    else:  # FIXED
        discount_amount = min(discount.value, applicable_amount)

    # Ensure discount doesn't exceed applicable amount
    discount_amount = min(discount_amount, applicable_amount)
    final_amount = max(original_amount - discount_amount, 0)

    # Increment usage count
    discount.current_uses += 1
    db.add(discount)

    return final_amount, discount_amount, discount, applies_to_component


async def _mark_paid_and_apply(
    db: AsyncSession,
    payment: Payment,
    provider: str,
    provider_reference: str | None,
    paid_at: datetime | None,
    provider_payload: dict | None = None,
) -> Payment:
    # Reload and lock the payment row to avoid double application (e.g., webhook + verify racing)
    result = await db.execute(
        select(Payment).where(Payment.id == payment.id).with_for_update()
    )
    payment = result.scalar_one()

    # IDEMPOTENCY CHECK: If payment is already fully processed, skip reprocessing
    # This prevents double-crediting when webhook and manual verify race
    if payment.status == PaymentStatus.PAID and payment.entitlement_applied_at:
        logger.info(
            f"Payment {payment.reference} already processed (status=PAID, entitlement applied at {payment.entitlement_applied_at}), skipping",
            extra={
                "extra_fields": {
                    "payment_id": str(payment.id),
                    "reference": payment.reference,
                }
            },
        )
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

    await _apply_entitlement_with_tracking(payment)

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
                        from libs.common.datetime_utils import utc_now

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
                f"{settings.ACADEMY_SERVICE_URL}/academy/internal/enrollments/{payload.enrollment_id}",
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
            paid_at=datetime.now(timezone.utc),
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

    await _apply_entitlement_with_tracking(payment)

    db.add(payment)
    await db.commit()
    await db.refresh(payment)
    return payment


@router.post("/admin/{reference}/replay-entitlement", response_model=PaymentResponse)
async def replay_payment_entitlement(
    reference: str,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Replay entitlement fulfillment for a paid payment."""
    result = await db.execute(select(Payment).where(Payment.reference == reference))
    payment = result.scalar_one_or_none()
    if not payment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found"
        )
    if payment.status != PaymentStatus.PAID:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Payment is not paid (status={payment.status.value})",
        )

    await _apply_entitlement_with_tracking(payment)
    db.add(payment)
    await db.commit()
    await db.refresh(payment)

    logger.info(
        "Entitlement replay requested by %s for payment %s",
        current_user.user_id,
        reference,
    )
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


# --- Discount Preview Endpoint ---


class DiscountPreviewRequest(BaseModel):
    code: str
    purpose: str  # e.g., "club", "community", "club_bundle", "academy_cohort"
    subtotal: float  # The pre-discount total amount
    # Component breakdown for smart discount matching (optional)
    components: dict[str, float] | None = (
        None  # e.g., {"community": 20000, "club": 150000}
    )


class DiscountPreviewResponse(BaseModel):
    valid: bool
    code: str
    discount_type: str | None = None  # "PERCENTAGE" or "FIXED"
    discount_value: float | None = None  # e.g., 75 for 75% or 5000 for ₦5000
    discount_amount: float = 0  # The actual amount to be deducted
    final_total: float  # The total after discount
    applies_to_component: str | None = None  # Which component the discount applies to
    message: str | None = None


@router.post("/discounts/preview", response_model=DiscountPreviewResponse)
async def preview_discount(
    payload: DiscountPreviewRequest,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Preview a discount code without creating a payment.
    Returns the calculated discount amount for display before checkout.
    Does NOT increment usage count.

    Smart Component Matching:
    - If discount applies to COMMUNITY and payment is CLUB_BUNDLE,
      discount only applies to the COMMUNITY portion.
    """
    from libs.common.datetime_utils import utc_now

    code = payload.code.upper().strip()

    # Lookup discount code
    query = select(Discount).where(
        Discount.code == code,
        Discount.is_active.is_(True),
    )
    result = await db.execute(query)
    discount = result.scalar_one_or_none()

    if not discount:
        return DiscountPreviewResponse(
            valid=False,
            code=code,
            final_total=payload.subtotal,
            message="Invalid discount code",
        )

    now = utc_now()

    # Check validity period
    if discount.valid_from and discount.valid_from > now:
        return DiscountPreviewResponse(
            valid=False,
            code=code,
            final_total=payload.subtotal,
            message="Discount code is not yet active",
        )

    if discount.valid_until and discount.valid_until < now:
        return DiscountPreviewResponse(
            valid=False,
            code=code,
            final_total=payload.subtotal,
            message="Discount code has expired",
        )

    # Check usage limits
    if discount.max_uses and discount.current_uses >= discount.max_uses:
        return DiscountPreviewResponse(
            valid=False,
            code=code,
            final_total=payload.subtotal,
            message="Discount code has reached its usage limit",
        )

    # Smart Component Matching
    # For bundle payments, check if discount applies to any individual component
    applicable_purposes = [p.upper() for p in (discount.applies_to or [])]
    purpose_upper = payload.purpose.upper()

    # Determine what amount the discount applies to
    applicable_amount = payload.subtotal
    applies_to_component = None

    if applicable_purposes:
        # Direct match - discount applies to the exact purpose
        if purpose_upper in applicable_purposes:
            applicable_amount = payload.subtotal
            applies_to_component = purpose_upper.lower()

        # Smart component matching for bundles
        elif purpose_upper == "CLUB_BUNDLE" and payload.components:
            # Check if discount applies to COMMUNITY portion
            if "COMMUNITY" in applicable_purposes and "community" in payload.components:
                applicable_amount = payload.components["community"]
                applies_to_component = "community"
            # Check if discount applies to CLUB portion
            elif "CLUB" in applicable_purposes and "club" in payload.components:
                applicable_amount = payload.components["club"]
                applies_to_component = "club"
            else:
                # Discount doesn't apply to any component in the bundle
                return DiscountPreviewResponse(
                    valid=False,
                    code=code,
                    final_total=payload.subtotal,
                    message="Discount code does not apply to any component in this payment",
                )
        else:
            # Discount doesn't apply to this purpose
            return DiscountPreviewResponse(
                valid=False,
                code=code,
                final_total=payload.subtotal,
                message=f"Discount code does not apply to {payload.purpose} payments",
            )

    # Calculate discount amount based on applicable amount
    if discount.discount_type == DiscountType.PERCENTAGE:
        discount_amount = applicable_amount * (discount.value / 100)
    else:  # FIXED
        discount_amount = min(discount.value, applicable_amount)

    # Ensure discount doesn't exceed applicable amount
    discount_amount = min(discount_amount, applicable_amount)
    final_total = max(payload.subtotal - discount_amount, 0)

    # Build message
    if applies_to_component:
        component_label = applies_to_component.replace("_", " ").title()
        message = f"{discount.value}{'%' if discount.discount_type == DiscountType.PERCENTAGE else ' NGN'} discount applied to {component_label}"
    else:
        message = f"{discount.value}{'%' if discount.discount_type == DiscountType.PERCENTAGE else ' NGN'} discount applied"

    return DiscountPreviewResponse(
        valid=True,
        code=discount.code,
        discount_type=discount.discount_type.value,
        discount_value=discount.value,
        discount_amount=discount_amount,
        final_total=final_total,
        applies_to_component=applies_to_component,
        message=message,
    )


# ---------------------------------------------------------------------------
# Internal service-to-service endpoints
# ---------------------------------------------------------------------------


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
    return {"deleted": True}


# ========================================================================
# Manual Payment Endpoints
# ========================================================================

from services.payments_service.schemas import AdminReviewRequest, SubmitProofRequest


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

    from datetime import datetime, timezone

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
