"""Apply entitlement for PaymentPurpose.CLUB_BUNDLE payments.

Extracted from the single-function `_apply_entitlement` dispatcher per
docs/CONVENTIONS.md §12. Each handler owns its own cross-service
contract end-to-end; the dispatcher (`_dispatcher._apply_entitlement`)
just routes by `payment.purpose`.
"""

import httpx
from fastapi import HTTPException, status

from libs.auth.dependencies import _service_role_jwt
from libs.common.config import get_settings
from services.payments_service.models import (
    Payment,
)

settings = get_settings()


async def apply_club_bundle(payment: Payment) -> None:
    years = int((payment.payment_metadata or {}).get("years") or 1)
    months = int((payment.payment_metadata or {}).get("months") or 1)
    headers = {"Authorization": f"Bearer {_service_role_jwt('payments')}"}
    async with httpx.AsyncClient(timeout=30) as client:
        community_resp = await client.post(
            f"{settings.MEMBERS_SERVICE_URL}/admin/members/by-auth/{payment.member_auth_id}/community/activate",
            json={
                "years": years,
                "idempotency_key": f"payment:{payment.id}:community-activate",
                "source_reference": payment.reference,
            },
            headers=headers,
        )
        if community_resp.status_code >= 400:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to apply community entitlement via members_service ({community_resp.status_code}): {community_resp.text}",
            )
        club_resp = await client.post(
            f"{settings.MEMBERS_SERVICE_URL}/admin/members/by-auth/{payment.member_auth_id}/club/activate",
            json={
                "months": months,
                "skip_community_check": True,
                "idempotency_key": f"payment:{payment.id}:club-activate",
                "source_reference": payment.reference,
            },
            headers=headers,
        )
        if club_resp.status_code >= 400:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to apply club entitlement via members_service ({club_resp.status_code}): {club_resp.text}",
            )
