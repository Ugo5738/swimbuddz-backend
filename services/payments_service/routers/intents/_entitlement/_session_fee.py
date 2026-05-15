"""Apply entitlement for PaymentPurpose.SESSION_FEE payments.

Extracted from the single-function `_apply_entitlement` dispatcher per
docs/CONVENTIONS.md §12. Each handler owns its own cross-service
contract end-to-end; the dispatcher (`_dispatcher._apply_entitlement`)
just routes by `payment.purpose`.
"""

import httpx
from fastapi import HTTPException, status

from libs.auth.dependencies import _service_role_jwt
from libs.common.config import get_settings
from libs.common.currency import KOBO_PER_NAIRA
from libs.common.logging import get_logger
from datetime import datetime, timezone
from libs.common.emails.client import get_email_client
from libs.common.service_client import internal_post
from services.payments_service.models import (
    Payment,
    PaymentPurpose,
    PaymentStatus,
)
from services.payments_service.schemas import (
    SessionAttendanceRole,
    SessionAttendanceStatus,
)

from .._helpers import (
    _require_attendance_status,
    _send_tier_activated_email,
    _update_pending_payment_reference,
)

settings = get_settings()
logger = get_logger(__name__)

async def apply_session_fee(payment: Payment) -> None:
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
