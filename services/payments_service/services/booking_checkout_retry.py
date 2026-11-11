"""Replace or abandon a verified unpaid booking checkout without pretending to revoke it.

Paystack's abandoned links may remain payable. Keep the original receipt and
mark it superseded/failed locally so any late payment goes to reconciliation
instead of fulfilling the booking or capturing its Bubbles a second time.
"""

from datetime import datetime

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.currency import naira_to_kobo
from libs.common.datetime_utils import utc_now
from services.payments_service.models import Payment, PaymentStatus
from services.payments_service.routers.intents._paystack import (
    _verify_paystack_transaction,
)


def is_verified_unpaid_paystack_tx(payment: Payment, data: dict) -> bool:
    """Confirm Paystack verification unambiguously describes an unpaid attempt."""
    return (
        data.get("status") in {"abandoned", "failed"}
        and data.get("reference") == payment.reference
        and data.get("amount") == naira_to_kobo(payment.amount)
        and data.get("currency") == payment.currency
        and not data.get("paid_at")
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
    if not is_verified_unpaid_paystack_tx(payment, data):
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


async def abandon_open_paystack_booking_checkout(
    db: AsyncSession, payment: Payment
) -> dict:
    """Verify an open Paystack booking checkout and close it locally if unpaid.

    Called when the member explicitly requests to switch payment options after
    backing out of Paystack, instead of waiting for background worker polling.
    """
    from services.payments_service.routers.intents._entitlement import (
        _mark_paid_and_apply,
    )
    from services.payments_service.routers.intents._helpers import (
        _clear_pending_tier_payment_for_payment,
        _release_bubbles_hold,
    )
    from services.payments_service.services.provider_failure import (
        record_provider_failure,
    )

    meta = payment.payment_metadata or {}
    has_auth_url = bool((meta.get("paystack") or {}).get("authorization_url"))
    try:
        data = await _verify_paystack_transaction(payment.reference, _max_retries=1)
    except HTTPException as exc:
        detail_str = str(exc.detail or "").lower()
        if not has_auth_url and (
            "(404)" in detail_str or "transaction reference not found" in detail_str
        ):
            changed = await record_provider_failure(
                db,
                payment,
                {
                    "verify_error": exc.detail,
                    "source": "member_switch_uninitialized",
                },
            )
            if not changed:
                return {
                    "outcome": "already_paid",
                    "reference": payment.reference,
                    "message": "Your earlier online payment just cleared! Session fee settled.",
                }
            await _release_bubbles_hold(payment)
            await _clear_pending_tier_payment_for_payment(payment)
            return {
                "outcome": "closed_unpaid",
                "reference": payment.reference,
                "message": "Previous incomplete attempt closed. You can choose how to pay now.",
            }
        raise HTTPException(
            503,
            "We could not check the earlier payment with Paystack. Please try again shortly.",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            503,
            "We could not check the earlier payment with Paystack. Please try again shortly.",
        ) from exc

    tx_status = str(data.get("status") or "").lower()
    if tx_status == "success":
        amount_kobo = int(data.get("amount") or 0)
        expected_kobo = naira_to_kobo(payment.amount)
        if (
            data.get("reference") != payment.reference
            or data.get("currency") != payment.currency
            or amount_kobo != expected_kobo
        ):
            raise HTTPException(
                409,
                "Earlier payment details do not match; Admin reconciliation is required.",
            )
        paid_at = None
        paid_at_str = data.get("paid_at")
        if isinstance(paid_at_str, str) and paid_at_str:
            try:
                paid_at = datetime.fromisoformat(paid_at_str.replace("Z", "+00:00"))
            except ValueError:
                paid_at = None
        await _mark_paid_and_apply(
            db=db,
            payment=payment,
            provider="paystack",
            provider_reference=payment.reference,
            paid_at=paid_at,
            provider_payload={"verify": data, "source": "member_switch_verify"},
        )
        return {
            "outcome": "already_paid",
            "reference": payment.reference,
            "message": "Your earlier online payment actually succeeded! Your session fee is settled.",
        }

    if is_verified_unpaid_paystack_tx(payment, data):
        changed = await record_provider_failure(
            db,
            payment,
            {"verify": data, "source": "member_switch_method"},
        )
        if not changed:
            return {
                "outcome": "already_paid",
                "reference": payment.reference,
                "message": "Your earlier online payment just cleared! Session fee settled.",
            }
        await _release_bubbles_hold(payment)
        await _clear_pending_tier_payment_for_payment(payment)
        return {
            "outcome": "closed_unpaid",
            "reference": payment.reference,
            "message": "Previous unpaid online attempt closed. You can now switch payment options.",
        }

    raise HTTPException(
        409,
        "Your earlier online payment is still active or processing with Paystack. Resume it or wait a moment before switching.",
    )
