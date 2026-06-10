"""Apply entitlement for PaymentPurpose.STROKELAB_FOUNDING payments.

When a Stroke Lab founding-member payment clears, the webhook-driven
dispatcher calls this handler, which tells ai_service to record the
founding-member row. ai_service owns the strokelab_founding_members
table; payments_service only knows it must poke ai_service on success
(same contract shape as apply_wallet_topup → wallet_service).

Idempotent: ai_service's /internal/ai/founding-members/confirm upserts
on member_auth_id, so a webhook redelivery or a concurrent client
/claim won't create a duplicate.
"""

from fastapi import HTTPException, status

from libs.common.config import get_settings
from libs.common.logging import get_logger
from libs.common.service_client import internal_post
from services.payments_service.models import Payment

settings = get_settings()
logger = get_logger(__name__)


async def apply_strokelab_founding(payment: Payment) -> None:
    resp = await internal_post(
        service_url=settings.AI_SERVICE_URL,
        path="/internal/ai/founding-members/confirm",
        calling_service="payments",
        json={
            "member_auth_id": str(payment.member_auth_id),
            "payment_reference": payment.reference,
            # payment.amount is in Naira; the founding row stores kobo.
            "amount_kobo": int(round(payment.amount * 100)),
        },
        timeout=30.0,
    )
    if resp.status_code >= 400:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                f"Failed to confirm Stroke Lab founding member "
                f"({resp.status_code}): {resp.text}"
            ),
        )
