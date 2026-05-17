"""Apply entitlement for PaymentPurpose.WALLET_TOPUP payments.

Extracted from the single-function `_apply_entitlement` dispatcher per
docs/CONVENTIONS.md §12. Each handler owns its own cross-service
contract end-to-end; the dispatcher (`_dispatcher._apply_entitlement`)
just routes by `payment.purpose`.
"""

from fastapi import HTTPException, status

from libs.common.config import get_settings
from libs.common.logging import get_logger
from libs.common.service_client import internal_post
from services.payments_service.models import (
    Payment,
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
