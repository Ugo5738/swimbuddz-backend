"""Apply entitlement for PaymentPurpose.STORE_ORDER payments.

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


async def apply_store_order(payment: Payment) -> None:
    order_id = (payment.payment_metadata or {}).get("order_id")
    if not order_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="order_id missing in payment metadata",
        )
    headers = {"Authorization": f"Bearer {_service_role_jwt('payments')}"}
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{settings.STORE_SERVICE_URL}/store/admin/orders/{order_id}/mark-paid",
            headers=headers,
        )
        if resp.status_code >= 400:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to mark store order as paid ({resp.status_code}): {resp.text}",
            )
    # No pending_payment_reference to clear for store orders
