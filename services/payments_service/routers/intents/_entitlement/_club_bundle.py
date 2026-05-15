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
from libs.common.currency import KOBO_PER_NAIRA
from libs.common.logging import get_logger
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

async def apply_club_bundle(payment: Payment) -> None:
    years = int((payment.payment_metadata or {}).get("years") or 1)
    months = int((payment.payment_metadata or {}).get("months") or 1)
    headers = {"Authorization": f"Bearer {_service_role_jwt('payments')}"}
    async with httpx.AsyncClient(timeout=30) as client:
        community_resp = await client.post(
            f"{settings.MEMBERS_SERVICE_URL}/admin/members/by-auth/{payment.member_auth_id}/community/activate",
            json={"years": years},
            headers=headers,
        )
        if community_resp.status_code >= 400:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to apply community entitlement via members_service ({community_resp.status_code}): {community_resp.text}",
            )
        club_resp = await client.post(
            f"{settings.MEMBERS_SERVICE_URL}/admin/members/by-auth/{payment.member_auth_id}/club/activate",
            json={"months": months, "skip_community_check": True},
            headers=headers,
        )
        if club_resp.status_code >= 400:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to apply club entitlement via members_service ({club_resp.status_code}): {club_resp.text}",
            )
    # Clear pending payment reference on success
    await _update_pending_payment_reference(payment.member_auth_id, None)
    # Send club tier email (bundle pays for both community + club)
    duration = f"{months} month{'s' if months != 1 else ''} Club + {years} year{'s' if years != 1 else ''} Community"
    await _send_tier_activated_email(payment, tier="club", duration=duration)
