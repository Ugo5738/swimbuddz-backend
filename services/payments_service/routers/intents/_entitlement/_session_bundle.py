"""Apply entitlement for PaymentPurpose.SESSION_BUNDLE payments.

Extracted from the single-function `_apply_entitlement` dispatcher per
docs/CONVENTIONS.md §12. Each handler owns its own cross-service
contract end-to-end; the dispatcher (`_dispatcher._apply_entitlement`)
just routes by `payment.purpose`.
"""

from datetime import datetime

import httpx
from fastapi import HTTPException, status

from libs.auth.dependencies import _service_role_jwt
from libs.common.config import get_settings
from libs.common.currency import bubbles_to_naira
from libs.common.emails.client import get_email_client
from libs.common.logging import get_logger
from services.payments_service.models import Payment

from .._helpers import _debit_bubbles

settings = get_settings()
logger = get_logger(__name__)


async def apply_session_bundle(payment: Payment) -> None:
    session_ids = (payment.payment_metadata or {}).get("session_ids") or []
    booking_ids = (payment.payment_metadata or {}).get("booking_ids") or []
    if not session_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="session_ids missing in payment metadata",
        )
    if len(booking_ids) != len(session_ids):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="booking_ids missing or incomplete in payment metadata",
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

        # Partial Bubbles: the intent reduced the Paystack charge by the Bubbles
        # value (see intent_creation `bubbles_purposes`); debit the wallet for
        # the Bubbles portion now that Paystack cleared the remainder.
        wallet_transaction_id = await _debit_bubbles(
            client, payment, reference_type="session_bundle"
        )

        # Confirm all reservations in one sessions-service transaction.
        # Attendance is only recorded at check-in; payment is not attendance.
        booking_resp = await client.post(
            f"{settings.SESSIONS_SERVICE_URL}/internal/sessions/bookings/bundle/confirm",
            json={
                "member_auth_id": payment.member_auth_id,
                "payment_intent_id": str(payment.id),
                "booking_ids": booking_ids,
                "wallet_transaction_id": wallet_transaction_id,
            },
            headers=headers,
        )
        if booking_resp.status_code >= 400:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=(
                    "Bundle booking confirmation failed "
                    f"({booking_resp.status_code}): {booking_resp.text}"
                ),
            )
        confirmed = list(session_ids)

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
                        "passengers": ride_cfg.get("passengers"),
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
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=(
                        f"Bundle ride fulfillment incomplete: {len(ride_created)} "
                        f"created, {len(ride_failed)} failed"
                    ),
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
                or bubbles_to_naira(bundle_bubbles)
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
                    if session_id not in confirmed:
                        continue
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
                    f"Bundle confirmation emails sent ({len(confirmed)}) to {member_email}"
                )
        except Exception as e:
            logger.error(f"Failed to send bundle confirmation emails: {e}")

    # Clear pending payment reference on success
