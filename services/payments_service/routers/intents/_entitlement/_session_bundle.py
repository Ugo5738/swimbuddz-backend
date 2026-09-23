"""Apply entitlement for PaymentPurpose.SESSION_BUNDLE payments.

Extracted from the single-function `_apply_entitlement` dispatcher per
docs/CONVENTIONS.md §12. Each handler owns its own cross-service
contract end-to-end; the dispatcher (`_dispatcher._apply_entitlement`)
just routes by `payment.purpose`.
"""

import httpx
from fastapi import HTTPException, status

from libs.auth.dependencies import _service_role_jwt
from libs.common.config import get_settings
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
                "confirmation_details": {
                    "amount_paid": float(payment.amount),
                    "currency": payment.currency,
                    "bubbles_applied": int(
                        (payment.payment_metadata or {}).get("bubbles_to_apply") or 0
                    ),
                    "bubbles_amount_ngn": float(
                        (payment.payment_metadata or {}).get("bubbles_value_ngn") or 0
                    ),
                    "payment_reference": payment.reference,
                },
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

        # Sessions owns the per-booking confirmation outbox, including retries.
