"""Fulfil guest-session payments in sessions_service.

Historically PaymentPurpose.GUEST_PASS meant only a pre-created GuestPass.
Door walk-ins now keep their canonical SessionParticipant identity while using
the same guest-session payment purpose and Payments/ledger machinery.
"""

import httpx
from fastapi import HTTPException, status

from libs.auth.dependencies import _service_role_jwt
from libs.common.config import get_settings
from services.payments_service.models import Payment

settings = get_settings()


async def apply_guest_pass(payment: Payment) -> None:
    metadata = payment.payment_metadata or {}
    participant_id = metadata.get("session_participant_id")
    guest_pass_id = metadata.get("guest_pass_id")
    headers = {"Authorization": f"Bearer {_service_role_jwt('payments')}"}

    if participant_id:
        amount_kobo = int(
            metadata.get("subtotal_kobo")
            or round(float(payment.amount or 0) * 100)
        )
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{settings.SESSIONS_SERVICE_URL}"
                f"/internal/sessions/participants/{participant_id}/confirm-payment",
                json={
                    "payment_reference": payment.reference,
                    "payment_method": payment.payment_method
                    or payment.provider
                    or "unknown",
                    "amount_kobo": amount_kobo,
                    "paid_at": (
                        payment.paid_at.isoformat() if payment.paid_at else None
                    ),
                },
                headers=headers,
            )
        if response.status_code >= 400:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=(
                    "Failed to settle guest walk-in via sessions_service "
                    f"({response.status_code}): {response.text}"
                ),
            )
        return

    if not guest_pass_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Guest-session payment is missing guest_pass_id/session_participant_id",
        )
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{settings.SESSIONS_SERVICE_URL}/internal/sessions/guest-passes/{guest_pass_id}/confirm",
            json={"payment_reference": payment.reference},
            headers=headers,
        )
    if response.status_code >= 400:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                "Failed to confirm guest pass via sessions_service "
                f"({response.status_code}): {response.text}"
            ),
        )
