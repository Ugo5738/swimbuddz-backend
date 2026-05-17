"""Apply entitlement for PaymentPurpose.CLUB payments.

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

from .._helpers import (
    _send_tier_activated_email,
    _update_pending_payment_reference,
)

settings = get_settings()
logger = get_logger(__name__)


async def apply_club(payment: Payment) -> None:
    months = int((payment.payment_metadata or {}).get("months") or 1)
    community_extension_months = int(
        (payment.payment_metadata or {}).get("community_extension_months") or 0
    )

    headers = {"Authorization": f"Bearer {_service_role_jwt('payments')}"}
    async with httpx.AsyncClient(timeout=30) as client:
        # If community extension was included, extend Community first
        if community_extension_months > 0:
            community_resp = await client.post(
                f"{settings.MEMBERS_SERVICE_URL}/admin/members/by-auth/{payment.member_auth_id}/community/extend",
                json={"months": community_extension_months},
                headers=headers,
            )
            if community_resp.status_code >= 400:
                logger.warning(f"Failed to extend community: {community_resp.text}")

        # Activate Club
        club_resp = await client.post(
            f"{settings.MEMBERS_SERVICE_URL}/admin/members/by-auth/{payment.member_auth_id}/club/activate",
            json={"months": months},
            headers=headers,
        )
        if club_resp.status_code >= 400:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to apply club entitlement via members_service ({club_resp.status_code}): {club_resp.text}",
            )
    # Clear pending payment reference on success
    await _update_pending_payment_reference(payment.member_auth_id, None)
    duration = f"{months} month{'s' if months != 1 else ''}"
    await _send_tier_activated_email(payment, tier="club", duration=duration)
