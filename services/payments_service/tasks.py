"""Background reconciliation tasks for payments service."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from libs.common.logging import get_logger
from libs.db.config import AsyncSessionLocal
from services.payments_service.models import Payment, PaymentStatus
from sqlalchemy import select

logger = get_logger(__name__)


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _payment_next_retry_at(payment: Payment) -> datetime | None:
    metadata = payment.payment_metadata or {}
    fulfillment = metadata.get("fulfillment") or {}
    raw_next = fulfillment.get("next_retry_at")
    if not isinstance(raw_next, str):
        return None
    return _parse_iso(raw_next)


async def reconcile_pending_paystack_payments() -> None:
    """Verify stale pending Paystack payments and advance state."""
    from services.payments_service.router import (
        _mark_paid_and_apply,
        _verify_paystack_transaction,
    )

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=2)
    processed = 0

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Payment)
            .where(
                Payment.status == PaymentStatus.PENDING,
                Payment.provider == "paystack",
                Payment.created_at <= cutoff,
            )
            .order_by(Payment.created_at.asc())
            .limit(200)
        )
        pending = list(result.scalars().all())

        for payment in pending:
            try:
                data = await _verify_paystack_transaction(payment.reference)
            except Exception as exc:
                logger.warning(
                    "Pending payment verify failed for %s: %s",
                    payment.reference,
                    exc,
                )
                continue

            status = str((data.get("status") or "")).lower()
            if status == "success":
                paid_at = None
                paid_at_str = data.get("paid_at")
                if isinstance(paid_at_str, str) and paid_at_str:
                    try:
                        paid_at = datetime.fromisoformat(
                            paid_at_str.replace("Z", "+00:00")
                        )
                    except ValueError:
                        paid_at = None
                await _mark_paid_and_apply(
                    db=db,
                    payment=payment,
                    provider="paystack",
                    provider_reference=payment.reference,
                    paid_at=paid_at,
                    provider_payload={"verify": data, "source": "payments_worker"},
                )
                processed += 1
            elif status in {"failed", "abandoned", "reversed"}:
                payment.status = PaymentStatus.FAILED
                payment.entitlement_error = f"Provider status: {status}"
                metadata = dict(payment.payment_metadata or {})
                metadata["provider_payload"] = {
                    "verify": data,
                    "source": "payments_worker",
                }
                payment.payment_metadata = metadata
                db.add(payment)
                await db.commit()
                processed += 1

    if processed:
        logger.info("Reconciled %d pending Paystack payments", processed)


async def retry_failed_entitlement_fulfillment() -> None:
    """Retry entitlement fulfillment for paid payments pending application."""
    from services.payments_service.router import _apply_entitlement_with_tracking

    now = datetime.now(timezone.utc)
    processed = 0

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Payment)
            .where(
                Payment.status == PaymentStatus.PAID,
                Payment.entitlement_applied_at.is_(None),
            )
            .order_by(Payment.updated_at.asc())
            .limit(200)
        )
        pending = list(result.scalars().all())

        for payment in pending:
            next_retry_at = _payment_next_retry_at(payment)
            if next_retry_at and next_retry_at > now:
                continue

            await _apply_entitlement_with_tracking(payment)
            db.add(payment)
            await db.commit()
            processed += 1

    if processed:
        logger.info("Retried entitlement fulfillment for %d payments", processed)
