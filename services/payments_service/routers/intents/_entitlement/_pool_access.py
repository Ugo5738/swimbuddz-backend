"""Pool Access entitlement is released exclusively after verified payment."""

import httpx
from fastapi import HTTPException
from libs.auth.dependencies import _service_role_jwt
from libs.common.config import get_settings
from libs.common.currency import naira_to_kobo
from services.payments_service.models import Payment


async def apply_pool_access(payment: Payment) -> None:
    booking_id = (payment.payment_metadata or {}).get("pool_access_booking_id")
    if not booking_id:
        raise HTTPException(422, "Pool Access booking missing from payment")
    settings = get_settings()
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{settings.POOLS_SERVICE_URL}/internal/pools/access/bookings/{booking_id}/confirm",
            json={
                "payment_reference": payment.reference,
                "member_auth_id": payment.member_auth_id,
                "amount_kobo": int(
                    (payment.payment_metadata or {}).get("pool_access_subtotal_kobo")
                    or naira_to_kobo(payment.amount)
                ),
            },
            headers={"Authorization": f"Bearer {_service_role_jwt('payments')}"},
        )
    if response.status_code >= 400:
        raise HTTPException(
            409 if response.status_code in {404, 409, 422} else 502,
            "Paid Pool Access reservation requires reconciliation: " + response.text,
        )
