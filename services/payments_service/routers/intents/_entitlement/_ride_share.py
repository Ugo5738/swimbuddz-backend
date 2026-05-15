"""Apply entitlement for PaymentPurpose.RIDE_SHARE payments.

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

async def apply_ride_share(payment: Payment) -> None:
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
