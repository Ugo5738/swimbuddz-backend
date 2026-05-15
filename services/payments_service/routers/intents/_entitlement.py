"""Entitlement application machinery.

`_apply_entitlement` is the cross-purpose dispatcher — one giant
if/elif chain across 9 PaymentPurpose values (COMMUNITY, CLUB,
CLUB_BUNDLE, ACADEMY_COHORT, STORE_ORDER, WALLET_TOPUP, SESSION_FEE,
SESSION_BUNDLE, RIDE_SHARE). Each branch issues the correct cross-service
calls (members_service for tier activation, wallet_service for topup,
academy_service for enrollment, etc.) when a payment lands in PAID.

`_apply_entitlement_with_tracking` is the wrapper route handlers + the
retry worker call: it records attempts in fulfillment metadata, schedules
the next retry or moves to dead-letter, and emits notifications / reward
events after success.

`_mark_paid_and_apply` flips a Payment from PENDING to PAID under a
SELECT ... FOR UPDATE lock so webhook + verify cannot double-apply.

SIZE NOTE (per docs/CONVENTIONS.md §12):
This file exceeds the 800-line router hard cap (1042 lines). It is
kept whole intentionally for this split: `_apply_entitlement` is one
logical dispatcher across PaymentPurpose values and its branches share
top-of-function setup (e.g. service-role headers). A natural follow-up
is to extract per-purpose handlers into a `_entitlement/` subpackage
(one module per purpose) once a few of the branches stabilise — that
is design work, not the line-redistribution we're doing here.
"""

import hashlib
import hmac
from datetime import datetime, timedelta, timezone
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

from ._helpers import _dispatch_payment_notification, _emit_membership_reward_events, _fulfillment_meta, _next_retry_time, _require_attendance_status, _send_tier_activated_email, _set_fulfillment_meta, _try_qualify_referral, _update_pending_payment_reference


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

        # Best-effort referral qualification after successful membership payment.
        # If this member was referred, their referral moves from "registered" → "rewarded"
        # and both referrer + referee get Bubbles.
        await _try_qualify_referral(payment.member_auth_id, payment.reference)

        # Best-effort reward events for membership payments
        await _emit_membership_reward_events(payment)

        # Best-effort: dispatch in-app payment confirmation notification
        await _dispatch_payment_notification(payment)
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
        # Determine whether this is a first paid activation vs renewal before mutation.
        community_event_type = "membership.renewed"
        headers = {"Authorization": f"Bearer {_service_role_jwt('payments')}"}
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                member_resp = await client.get(
                    f"{settings.MEMBERS_SERVICE_URL}/members/by-auth/{payment.member_auth_id}",
                    headers=headers,
                )
                if member_resp.status_code == 200:
                    member_data = member_resp.json() or {}
                    membership = member_data.get("membership") or {}
                    previous_paid_until = membership.get("community_paid_until")
                    if not previous_paid_until:
                        community_event_type = "membership.activated"
                else:
                    logger.warning(
                        "Could not determine community activation type for %s (status=%d)",
                        payment.reference,
                        member_resp.status_code,
                    )
        except Exception as exc:
            logger.warning(
                "Failed to determine community activation type for %s: %s",
                payment.reference,
                exc,
            )

        payment.payment_metadata = {
            **(payment.payment_metadata or {}),
            "community_reward_event_type": community_event_type,
        }
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
        duration = f"{months} month{'s' if months != 1 else ''}"
        await _send_tier_activated_email(payment, tier="club", duration=duration)
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
        # Send club tier email (bundle pays for both community + club)
        duration = f"{months} month{'s' if months != 1 else ''} Club + {years} year{'s' if years != 1 else ''} Community"
        await _send_tier_activated_email(payment, tier="club", duration=duration)
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
            # Pass the actual amount paid (kobo). When this exceeds the target
            # installment's stipulated amount (member chose a custom amount),
            # the academy mark-paid endpoint rolls forward across installments.
            "amount_kobo": int(round((payment.amount or 0) * KOBO_PER_NAIRA)),
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

            # Partial Bubbles: debit wallet for bubbles_to_apply before attendance.
            bubbles_to_apply = int(
                (payment.payment_metadata or {}).get("bubbles_to_apply") or 0
            )
            if bubbles_to_apply > 0:
                debit_resp = await client.post(
                    f"{settings.WALLET_SERVICE_URL}/internal/wallet/debit",
                    json={
                        "idempotency_key": f"session_fee_{payment.reference}",
                        "member_auth_id": payment.member_auth_id,
                        "amount": bubbles_to_apply,
                        "transaction_type": "purchase",
                        "description": f"Session booking (partial payment): {payment.reference}",
                        "service_source": "payments_service",
                        "reference_type": "session_fee",
                        "reference_id": str(payment.reference),
                    },
                    headers=headers,
                )
                if debit_resp.status_code >= 400:
                    # Log but don't fail — the Paystack portion was already charged.
                    # Record the failure in metadata so it's visible without log access.
                    # (Caller commits the payment after _apply_entitlement returns.)
                    logger.warning(
                        f"Wallet debit failed for session_fee {payment.reference}: "
                        f"{debit_resp.status_code} {debit_resp.text}"
                    )
                    payment.payment_metadata = {
                        **(payment.payment_metadata or {}),
                        "bubbles_debit_failed": True,
                        "bubbles_debit_error": f"{debit_resp.status_code}: {debit_resp.text[:200]}",
                    }
                else:
                    logger.info(
                        f"Wallet debit succeeded for session_fee {payment.reference}: "
                        f"{bubbles_to_apply} Bubbles"
                    )

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
                num_seats = (payment.payment_metadata or {}).get("num_seats", 1)
                transport_resp = await client.post(
                    f"{settings.TRANSPORT_SERVICE_URL}/transport/sessions/{session_id}/bookings",
                    json={
                        "session_ride_config_id": ride_config_id,
                        "pickup_location_id": pickup_location_id,
                        "num_seats": num_seats,
                    },
                    params={"member_id": str(member_id)},
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

                # Partial Bubbles details (if applied)
                fee_bubbles = int(
                    (payment.payment_metadata or {}).get("bubbles_to_apply") or 0
                )
                fee_bubbles_ngn = float(
                    (payment.payment_metadata or {}).get("bubbles_value_ngn")
                    or (fee_bubbles * 100)
                )

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
                            "bubbles_applied": fee_bubbles if fee_bubbles > 0 else None,
                            "bubbles_amount_ngn": (
                                fee_bubbles_ngn if fee_bubbles > 0 else None
                            ),
                        },
                    )
                    logger.info(f"Session confirmation email sent to {member_email}")
            except Exception as e:
                # Log but don't fail if email fails
                logger.error(f"Failed to send session confirmation email: {e}")

        # Clear pending payment reference on success
        await _update_pending_payment_reference(payment.member_auth_id, None)
        return

    # Handle session bundle payment — create attendance records for all sessions in bundle
    elif payment.purpose == PaymentPurpose.SESSION_BUNDLE:
        session_ids = (payment.payment_metadata or {}).get("session_ids") or []
        if not session_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="session_ids missing in payment metadata",
            )

        headers = {"Authorization": f"Bearer {_service_role_jwt('payments')}"}
        async with httpx.AsyncClient(timeout=30) as client:
            # Look up member_id from auth_id via members service
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

            # Create attendance record for each session in bundle
            created: list[str] = []
            failed: list[dict] = []
            for session_id in session_ids:
                att_resp = await client.post(
                    f"{settings.ATTENDANCE_SERVICE_URL}/attendance/sessions/{session_id}/attendance/public",
                    json={
                        "member_id": member_id,
                        "status": SessionAttendanceStatus.PRESENT.value,
                        "role": SessionAttendanceRole.SWIMMER.value,
                        "notes": f"Bundle payment ref: {payment.reference}",
                    },
                    headers=headers,
                )
                if att_resp.status_code >= 400:
                    failed.append({"session_id": session_id, "error": att_resp.text})
                    logger.warning(
                        f"Bundle attendance creation failed for session {session_id}: "
                        f"{att_resp.status_code} {att_resp.text}"
                    )
                else:
                    created.append(session_id)

            if failed and not created:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"All bundle attendance creations failed: {failed}",
                )
            if failed:
                logger.warning(
                    f"Bundle partial fulfillment: {len(created)} created, {len(failed)} failed"
                )

            # Create ride bookings for any sessions with ride configs in metadata.
            ride_configs = (payment.payment_metadata or {}).get(
                "session_ride_configs"
            ) or {}
            if ride_configs:
                ride_created: list[str] = []
                ride_failed: list[dict] = []
                for session_id, ride_cfg in ride_configs.items():
                    transport_resp = await client.post(
                        f"{settings.TRANSPORT_SERVICE_URL}/transport/sessions/{session_id}/bookings",
                        json={
                            "session_ride_config_id": ride_cfg.get("ride_config_id"),
                            "pickup_location_id": ride_cfg.get("pickup_location_id"),
                            "num_seats": int(ride_cfg.get("num_seats") or 1),
                        },
                        params={"member_id": str(member_id)},
                        headers=headers,
                    )
                    if transport_resp.status_code >= 400:
                        ride_failed.append(
                            {"session_id": session_id, "error": transport_resp.text}
                        )
                        logger.warning(
                            f"Bundle ride booking failed for session {session_id}: "
                            f"{transport_resp.status_code} {transport_resp.text}"
                        )
                    else:
                        ride_created.append(session_id)
                if ride_failed:
                    logger.warning(
                        f"Bundle ride partial fulfillment: {len(ride_created)} created, "
                        f"{len(ride_failed)} failed"
                    )

            # Send one confirmation email per booked session in the bundle.
            try:
                member_email = member_data.get("email") or payment.payer_email
                member_name = (
                    f"{member_data.get('first_name', '')} "
                    f"{member_data.get('last_name', '')}"
                ).strip()
                session_count = len(session_ids)
                per_session_amount = (
                    float(payment.amount) / session_count if session_count else 0.0
                )
                # Partial Bubbles applied to the bundle, pro-rated per session
                bundle_bubbles = int(
                    (payment.payment_metadata or {}).get("bubbles_to_apply") or 0
                )
                bundle_bubbles_ngn = float(
                    (payment.payment_metadata or {}).get("bubbles_value_ngn")
                    or (bundle_bubbles * 100)
                )
                per_session_bubbles = (
                    bundle_bubbles // session_count if session_count else 0
                )
                per_session_bubbles_ngn = (
                    bundle_bubbles_ngn / session_count if session_count else 0.0
                )
                if member_email:
                    email_client = get_email_client()
                    for idx, session_id in enumerate(session_ids, start=1):
                        if session_id not in created:
                            continue  # skip sessions whose attendance failed
                        try:
                            session_resp = await client.get(
                                f"{settings.SESSIONS_SERVICE_URL}/sessions/{session_id}",
                                headers=headers,
                            )
                            session_data = (
                                session_resp.json()
                                if session_resp.status_code < 400
                                else {}
                            )
                            starts_at = session_data.get("starts_at", "")
                            session_date = ""
                            session_time = ""
                            if starts_at:
                                try:
                                    dt = datetime.fromisoformat(
                                        starts_at.replace("Z", "+00:00")
                                    )
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
                                        starts_at[:10]
                                        if len(starts_at) >= 10
                                        else starts_at
                                    )

                            await email_client.send_template(
                                template_type="session_confirmation",
                                to_email=member_email,
                                template_data={
                                    "member_name": member_name or "Member",
                                    "member_id": str(member_id),
                                    "session_title": session_data.get(
                                        "title", "Swimming Session"
                                    ),
                                    "session_date": session_date,
                                    "session_time": session_time,
                                    "session_location": session_data.get(
                                        "location_name", ""
                                    )
                                    or session_data.get("location", ""),
                                    "session_address": session_data.get("address", ""),
                                    "amount_paid": per_session_amount,
                                    "currency": payment.currency,
                                    "bubbles_applied": (
                                        per_session_bubbles
                                        if per_session_bubbles > 0
                                        else None
                                    ),
                                    "bubbles_amount_ngn": (
                                        per_session_bubbles_ngn
                                        if per_session_bubbles > 0
                                        else None
                                    ),
                                    "bundle_info": (
                                        f"Session {idx} of {session_count} in your booking"
                                        if session_count > 1
                                        else None
                                    ),
                                },
                            )
                        except Exception as inner_e:
                            logger.warning(
                                "Failed to send bundle confirmation for session %s: %s",
                                session_id,
                                inner_e,
                            )
                    logger.info(
                        f"Bundle confirmation emails sent ({len(created)}) to {member_email}"
                    )
            except Exception as e:
                logger.error(f"Failed to send bundle confirmation emails: {e}")

        # Clear pending payment reference on success
        await _update_pending_payment_reference(payment.member_auth_id, None)
        return

    # Handle standalone Ride Share payment — create ride booking only (attendance already exists)
    elif payment.purpose == PaymentPurpose.RIDE_SHARE:
        session_id = (payment.payment_metadata or {}).get("session_id")
        ride_config_id = (payment.payment_metadata or {}).get("ride_config_id")
        pickup_location_id = (payment.payment_metadata or {}).get("pickup_location_id")
        num_seats = (payment.payment_metadata or {}).get("num_seats", 1)

        if not session_id or not ride_config_id or not pickup_location_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Missing ride share metadata (session_id, ride_config_id, pickup_location_id)",
            )

        headers = {"Authorization": f"Bearer {_service_role_jwt('payments')}"}
        async with httpx.AsyncClient(timeout=30) as client:
            # Look up member_id from auth_id via members service
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

            # Create ride booking via transport service — MUST succeed (it's the whole point)
            transport_resp = await client.post(
                f"{settings.TRANSPORT_SERVICE_URL}/transport/sessions/{session_id}/bookings",
                json={
                    "session_ride_config_id": ride_config_id,
                    "pickup_location_id": pickup_location_id,
                    "num_seats": num_seats,
                },
                params={"member_id": str(member_id)},
                headers=headers,
            )
            if transport_resp.status_code >= 400:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"Failed to create ride booking ({transport_resp.status_code}): {transport_resp.text}",
                )

            # Send ride share confirmation email (best-effort)
            try:
                session_resp = await client.get(
                    f"{settings.SESSIONS_SERVICE_URL}/sessions/{session_id}",
                    headers=headers,
                )
                session_data = (
                    session_resp.json() if session_resp.status_code < 400 else {}
                )

                member_email = member_data.get("email", "")
                member_name = f"{member_data.get('first_name', '')} {member_data.get('last_name', '')}".strip()

                starts_at = session_data.get("starts_at", "")
                session_date = ""
                session_time = ""
                if starts_at:
                    try:
                        dt = datetime.fromisoformat(starts_at.replace("Z", "+00:00"))
                        session_date = dt.strftime("%A, %B %d, %Y")
                        session_time = f"{dt.strftime('%I:%M %p')}"
                        ends_at = session_data.get("ends_at", "")
                        if ends_at:
                            end_dt = datetime.fromisoformat(
                                ends_at.replace("Z", "+00:00")
                            )
                            session_time += f" - {end_dt.strftime('%I:%M %p')}"
                    except Exception:
                        session_date = (
                            starts_at[:10] if len(starts_at) >= 10 else starts_at
                        )

                # Find ride share details from session data
                ride_share_area = None
                pickup_name = None
                pickup_description = None
                departure_time = None
                ride_areas = session_data.get("rideShareAreas", []) or session_data.get(
                    "ride_share_areas", []
                )
                for area in ride_areas:
                    if area.get("id") == ride_config_id:
                        ride_share_area = area.get("ride_area_name", "") or area.get(
                            "area_name", ""
                        )
                        for loc in area.get("pickup_locations", []):
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

                if member_email:
                    email_client = get_email_client()
                    await email_client.send_template(
                        template_type="ride_share_confirmation",
                        to_email=member_email,
                        template_data={
                            "member_name": member_name or "Member",
                            "session_title": session_data.get(
                                "title", "Swimming Session"
                            ),
                            "session_date": session_date,
                            "session_time": session_time,
                            "session_location": session_data.get("location_name", "")
                            or session_data.get("location", ""),
                            "amount_paid": float(payment.amount),
                            "ride_share_area": ride_share_area,
                            "pickup_location": pickup_name,
                            "pickup_description": pickup_description,
                            "departure_time": departure_time,
                            "num_seats": num_seats,
                            "currency": payment.currency,
                        },
                    )
                    logger.info(f"Ride share confirmation email sent to {member_email}")
            except Exception as e:
                logger.error(f"Failed to send ride share confirmation email: {e}")

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

    # Send tier activation email (only reaches here for COMMUNITY)
    if payment.purpose == PaymentPurpose.COMMUNITY:
        years = int((payment.payment_metadata or {}).get("years") or 1)
        duration = f"{years} year{'s' if years != 1 else ''}"
        await _send_tier_activated_email(payment, tier="community", duration=duration)


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

    # IDEMPOTENCY CHECK: If payment is already marked PAID, another caller
    # (webhook, verify, or reconciliation worker) owns entitlement processing.
    # We bail out unconditionally — the retry_failed_entitlement_fulfillment
    # worker calls _apply_entitlement_with_tracking directly and handles
    # retries for payments that were marked PAID but failed entitlement.
    if payment.status == PaymentStatus.PAID:
        logger.info(
            f"Payment {payment.reference} already PAID "
            f"(entitlement_applied_at={payment.entitlement_applied_at}), "
            f"skipping duplicate _mark_paid_and_apply call",
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
