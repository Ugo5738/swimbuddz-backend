"""Apply entitlement for PaymentPurpose.SESSION_BOOKING payments.

A1 Phase 3.3 Paystack pre-booking. The frontend:

  1. POST /api/v1/sessions/{id}/book (pay_with_bubbles=false)
       → sessions_service creates SessionBooking(status=PENDING),
         returns booking.id
  2. POST /api/v1/payments/intents
       purpose=session_booking, payment_metadata.booking_id=<id>
  3. member pays via Paystack
  4. payment verify → this handler runs → confirms the booking

This handler calls sessions_service's service-role endpoint
``POST /internal/sessions/bookings/{booking_id}/confirm`` with the
payment intent id (and wallet txn id, if partial Bubbles were applied),
flipping the booking PENDING → CONFIRMED. Idempotent: the confirm
endpoint returns the booking unchanged if it's already CONFIRMED.

Each handler owns its own cross-service contract end-to-end; the
dispatcher just routes by ``payment.purpose``. See docs/CONVENTIONS.md
§12 and docs/design/A1_SESSION_DISCRIMINATOR_REFACTOR.md §C.
"""

import httpx
from fastapi import HTTPException, status

from libs.auth.dependencies import _service_role_jwt
from libs.common.config import get_settings
from libs.common.logging import get_logger
from services.payments_service.models import Payment

from .._helpers import _debit_bubbles, _update_pending_payment_reference

settings = get_settings()
logger = get_logger(__name__)


async def apply_session_booking(payment: Payment) -> None:
    meta = payment.payment_metadata or {}
    booking_id = meta.get("booking_id")
    if not booking_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="booking_id missing in payment metadata",
        )

    headers = {"Authorization": f"Bearer {_service_role_jwt('payments')}"}
    async with httpx.AsyncClient(timeout=30) as client:
        # Partial Bubbles: the intent reduced the Paystack charge by the Bubbles
        # value (see intent_creation `bubbles_purposes`). Now that Paystack has
        # cleared the remainder, debit the wallet for the Bubbles portion. The
        # resulting wallet txn id is recorded on the booking for audit.
        wallet_transaction_id = await _debit_bubbles(
            client, payment, reference_type="session_booking"
        )

        resp = await client.post(
            f"{settings.SESSIONS_SERVICE_URL}"
            f"/internal/sessions/bookings/{booking_id}/confirm",
            json={
                "payment_intent_id": str(payment.id),
                "wallet_transaction_id": wallet_transaction_id,
            },
            headers=headers,
        )
        if resp.status_code == 404:
            # Booking expired (TTL) or was cancelled before payment cleared.
            # Surface as a hard failure so the retry/dead-letter machinery
            # records it; ops can then refund manually. Don't silently drop.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"SessionBooking {booking_id} not found at confirm time "
                    f"(likely expired before payment). Payment {payment.reference} "
                    f"needs manual refund."
                ),
            )
        if resp.status_code >= 400:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=(
                    f"Failed to confirm booking {booking_id} "
                    f"({resp.status_code}): {resp.text}"
                ),
            )

    logger.info(
        "SessionBooking %s confirmed via payment %s",
        booking_id,
        payment.reference,
    )

    # Clear pending payment reference on success (mirrors session_fee).
    await _update_pending_payment_reference(payment.member_auth_id, None)
