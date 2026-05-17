"""Entitlement dispatcher + retry tracking + paid-marker.

Public surface (re-exported by `_entitlement/__init__.py` and through
to `intents/__init__.py` for the retry worker and route modules):
  - `_apply_entitlement(payment)` — routes by `payment.purpose` to
    the matching `apply_<purpose>` handler. Raises 501 on unknown.
  - `_apply_entitlement_with_tracking(payment)` — wraps the
    dispatcher with attempt counting, exponential-backoff retry
    scheduling, dead-letter, and post-success notifications/reward
    events.
  - `_mark_paid_and_apply(...)` — flips PENDING → PAID under a
    SELECT ... FOR UPDATE lock and triggers fulfillment.
"""

from datetime import datetime

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.logging import get_logger
from libs.common.datetime_utils import utc_now
from services.payments_service.models import (
    Payment,
    PaymentPurpose,
    PaymentStatus,
)

from .._helpers import (
    _dispatch_payment_notification,
    _emit_membership_reward_events,
    _fulfillment_meta,
    _next_retry_time,
    _set_fulfillment_meta,
    _try_qualify_referral,
)

from ._academy_cohort import apply_academy_cohort
from ._club import apply_club
from ._club_bundle import apply_club_bundle
from ._community import apply_community
from ._ride_share import apply_ride_share
from ._session_booking import apply_session_booking
from ._session_bundle import apply_session_bundle
from ._session_fee import apply_session_fee
from ._store_order import apply_store_order
from ._wallet_topup import apply_wallet_topup

logger = get_logger(__name__)

# Constants used by _apply_entitlement_with_tracking for retry bookkeeping.
FULFILLMENT_META_KEY = "fulfillment"
MAX_FULFILLMENT_RETRIES = 8
BASE_FULFILLMENT_RETRY_MINUTES = 2

# Dispatch table — add new PaymentPurpose handlers by writing an apply_X
# module and adding the entry here. The dispatcher itself never grows.
_PURPOSE_HANDLERS = {
    PaymentPurpose.COMMUNITY: apply_community,
    PaymentPurpose.CLUB: apply_club,
    PaymentPurpose.CLUB_BUNDLE: apply_club_bundle,
    PaymentPurpose.ACADEMY_COHORT: apply_academy_cohort,
    PaymentPurpose.STORE_ORDER: apply_store_order,
    PaymentPurpose.WALLET_TOPUP: apply_wallet_topup,
    PaymentPurpose.SESSION_FEE: apply_session_fee,
    PaymentPurpose.SESSION_BUNDLE: apply_session_bundle,
    PaymentPurpose.SESSION_BOOKING: apply_session_booking,
    PaymentPurpose.RIDE_SHARE: apply_ride_share,
}


async def _apply_entitlement_with_tracking(payment: Payment) -> None:
    now = utc_now()
    existing = _fulfillment_meta(payment)
    attempts = int(existing.get("attempts") or 0) + 1

    _set_fulfillment_meta(
        payment,
        status="in_progress",
        attempts=attempts,
        last_attempt_at=now.isoformat(),
    )

    try:
        await _apply_entitlement(payment)
        payment.entitlement_applied_at = now
        payment.entitlement_error = None
        _set_fulfillment_meta(
            payment,
            status="applied",
            next_retry_at=None,
            last_error=None,
        )

        # Best-effort referral qualification after successful membership payment.
        # If this member was referred, their referral moves from "registered" → "rewarded"
        # and both referrer + referee get Bubbles.
        await _try_qualify_referral(payment.member_auth_id, payment.reference)

        # Best-effort reward events for membership payments
        await _emit_membership_reward_events(payment)

        # Best-effort: dispatch in-app payment confirmation notification
        await _dispatch_payment_notification(payment)
    except Exception as exc:
        error_message = str(exc)
        payment.entitlement_error = error_message

        if attempts >= MAX_FULFILLMENT_RETRIES:
            _set_fulfillment_meta(
                payment,
                status="dead_letter",
                next_retry_at=None,
                last_error=error_message,
            )
        else:
            retry_at = _next_retry_time(attempts)
            _set_fulfillment_meta(
                payment,
                status="retry_scheduled",
                next_retry_at=retry_at.isoformat(),
                last_error=error_message,
            )

        logger.warning(
            "Entitlement apply failed for %s (attempt %d/%d): %s",
            payment.reference,
            attempts,
            MAX_FULFILLMENT_RETRIES,
            error_message,
        )


async def _apply_entitlement(payment: Payment) -> None:
    """Route a paid Payment to the correct per-purpose handler.

    Each handler owns its own cross-service contract (members_service for
    tier activation, wallet_service for topup, academy_service for
    enrollment, etc.) and either completes successfully or raises an
    HTTPException — which `_apply_entitlement_with_tracking` translates
    into retry / dead-letter bookkeeping.

    Adding a new PaymentPurpose: implement `apply_<purpose>(payment)` in
    a sibling module and register it in `_PURPOSE_HANDLERS` above.
    """
    handler = _PURPOSE_HANDLERS.get(payment.purpose)
    if handler is None:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=f"Entitlement application not implemented for purpose={payment.purpose}",
        )
    await handler(payment)


async def _mark_paid_and_apply(
    db: AsyncSession,
    payment: Payment,
    provider: str,
    provider_reference: str | None,
    paid_at: datetime | None,
    provider_payload: dict | None = None,
) -> Payment:
    # Reload and lock the payment row to avoid double application (e.g., webhook + verify racing)
    result = await db.execute(
        select(Payment).where(Payment.id == payment.id).with_for_update()
    )
    payment = result.scalar_one()

    # IDEMPOTENCY CHECK: If payment is already marked PAID, another caller
    # (webhook, verify, or reconciliation worker) owns entitlement processing.
    # We bail out unconditionally — the retry_failed_entitlement_fulfillment
    # worker calls _apply_entitlement_with_tracking directly and handles
    # retries for payments that were marked PAID but failed entitlement.
    if payment.status == PaymentStatus.PAID:
        logger.info(
            f"Payment {payment.reference} already PAID "
            f"(entitlement_applied_at={payment.entitlement_applied_at}), "
            f"skipping duplicate _mark_paid_and_apply call",
            extra={
                "extra_fields": {
                    "payment_id": str(payment.id),
                    "reference": payment.reference,
                }
            },
        )
        return payment

    payment.status = PaymentStatus.PAID
    payment.provider = provider
    payment.provider_reference = provider_reference
    payment.paid_at = paid_at or utc_now()
    if provider_payload:
        payment.payment_metadata = {
            **(payment.payment_metadata or {}),
            "provider_payload": provider_payload,
        }

    db.add(payment)
    await db.commit()
    await db.refresh(payment)

    await _apply_entitlement_with_tracking(payment)

    db.add(payment)
    await db.commit()
    await db.refresh(payment)
    return payment
