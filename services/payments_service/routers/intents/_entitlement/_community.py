"""Apply entitlement for PaymentPurpose.COMMUNITY payments.

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
from services.payments_service.models import (
    Payment,
)

settings = get_settings()
logger = get_logger(__name__)


async def apply_community(payment: Payment) -> None:
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
    payload = {
        "years": years,
        "idempotency_key": f"payment:{payment.id}:community-activate",
        "source_reference": payment.reference,
    }

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
