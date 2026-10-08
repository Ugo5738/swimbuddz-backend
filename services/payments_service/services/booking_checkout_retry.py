"""Replace a verified unpaid booking checkout without pretending to revoke it.

Paystack's abandoned links may remain payable. Keep the original receipt and
mark it superseded locally so any late payment goes to reconciliation instead
of fulfilling the booking or capturing its Bubbles a second time.
"""

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.currency import naira_to_kobo
from libs.common.datetime_utils import utc_now
from services.payments_service.models import Payment, PaymentStatus
from services.payments_service.routers.intents._paystack import (
    _verify_paystack_transaction,
)


async def supersede_unpaid_booking_checkout(
    db: AsyncSession, payment: Payment, replacement_reference: str
) -> bool:
    """Caller holds the booking lock through replacement payment creation.

    Flush, never commit: the supersession must roll back if creating its
    replacement fails. Neither a local FAILED flag nor age alone is evidence.
    """
    meta = payment.payment_metadata or {}
    if (
        payment.status != PaymentStatus.FAILED
        or payment.provider != "paystack"
        or payment.paid_at
        or payment.entitlement_applied_at
        or meta.get("wallet_transaction_id")
        or meta.get("wallet_hold_status") == "captured"
        or payment.reference == replacement_reference
    ):
        return False
    try:
        data = await _verify_paystack_transaction(payment.reference, _max_retries=1)
    except Exception as exc:
        raise HTTPException(
            503,
            "We could not check the earlier payment. No new payment was started; please try again shortly.",
        ) from exc

    # Do not retire a receipt, a transfer in progress, a reversal, or a
    # response for the wrong transaction. The provider can change state
    # later; the durable marker below also guards that webhook path.
    if (
        data.get("status") not in {"abandoned", "failed"}
        or data.get("reference") != payment.reference
        or data.get("amount") != naira_to_kobo(payment.amount)
        or data.get("currency") != payment.currency
        or data.get("paid_at")
    ):
        return False

    payment.payment_metadata = {
        **meta,
        "booking_attempt_superseded": {
            "replacement_reference": replacement_reference,
            "at": utc_now().isoformat(),
            "member_auth_id": payment.member_auth_id,
            "verification": {
                key: data.get(key)
                for key in ("id", "reference", "status", "amount", "currency")
            },
        },
    }
    await db.flush()
    from services.payments_service.routers.intents._helpers import _release_bubbles_hold

    await _release_bubbles_hold(payment)
    return True
