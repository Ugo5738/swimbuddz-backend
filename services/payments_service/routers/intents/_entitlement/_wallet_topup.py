"""Apply entitlement for PaymentPurpose.WALLET_TOPUP payments.

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

async def apply_wallet_topup(payment: Payment) -> None:
    topup_reference = (payment.payment_metadata or {}).get(
        "topup_reference"
    ) or payment.reference
    resp = await internal_post(
        service_url=settings.WALLET_SERVICE_URL,
        path="/internal/wallet/confirm-topup",
        calling_service="payments",
        json={
            "topup_reference": topup_reference,
            "payment_reference": payment.reference,
            "status": "completed",
        },
        timeout=30.0,
    )
    if resp.status_code >= 400:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to confirm wallet topup ({resp.status_code}): {resp.text}",
        )
