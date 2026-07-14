"""Private helpers for the enrollments router package.

Used by `me.withdraw_my_enrollment` to annotate paid payments with the refund
obligation in payments_service metadata.
"""

from typing import Optional

from libs.common.config import get_settings
from libs.common.logging import get_logger
from libs.common.service_client import internal_post

logger = get_logger(__name__)


async def _annotate_payment_with_refund(
    *,
    payment_reference: str,
    refund_kobo: int,
    enrollment_id: str,
    window: str,
    reason: Optional[str],
    calling_service: str,
) -> None:
    """Best-effort: annotate a payment with refund obligation in its metadata.

    Calls payments_service internal endpoint. If it fails, the withdrawal
    still completes — admins can manually reconcile from the academy-side
    record (enrollment + installments).
    """
    try:
        _settings = get_settings()
        await internal_post(
            service_url=_settings.PAYMENTS_SERVICE_URL,
            path=f"/internal/payments/{payment_reference}/annotate-refund",
            calling_service=calling_service,
            json={
                "refund_kobo": refund_kobo,
                "enrollment_id": enrollment_id,
                "window": window,
                "reason": reason,
            },
        )
    except Exception:
        logger.warning(
            "Failed to annotate payment %s with refund obligation (best-effort)",
            payment_reference,
            exc_info=True,
        )
