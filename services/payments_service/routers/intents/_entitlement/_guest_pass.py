"""Fulfil a standalone guest-pass payment in sessions_service."""

import httpx
from fastapi import HTTPException, status

from libs.auth.dependencies import _service_role_jwt
from libs.common.config import get_settings
from services.payments_service.models import Payment

settings = get_settings()


async def apply_guest_pass(payment: Payment) -> None:
    guest_pass_id = (payment.payment_metadata or {}).get("guest_pass_id")
    if not guest_pass_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Guest-pass payment is missing guest_pass_id",
        )
    headers = {"Authorization": f"Bearer {_service_role_jwt('payments')}"}
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
