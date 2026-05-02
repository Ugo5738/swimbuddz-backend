"""Background reconciliation tasks for payments service."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from libs.db.config import AsyncSessionLocal
from services.payments_service.models import (
    CoachPayout,
    CohortMakeupObligation,
    MakeupStatus,
    Payment,
    PaymentStatus,
    PayoutStatus,
    RecurringPayoutConfig,
    RecurringPayoutStatus,
)
from services.payments_service.services.payout_calculator import compute_block_payout
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
    from services.payments_service.routers.intents import (
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
    from services.payments_service.routers.intents import (
        _apply_entitlement_with_tracking,
    )

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
            # Skip dead-lettered payments (max retries exhausted)
            fulfillment = (payment.payment_metadata or {}).get("fulfillment") or {}
            if fulfillment.get("status") == "dead_letter":
                continue

            next_retry_at = _payment_next_retry_at(payment)
            if next_retry_at and next_retry_at > now:
                continue

            await _apply_entitlement_with_tracking(payment)
            db.add(payment)
            await db.commit()
            processed += 1

    if processed:
        logger.info("Retried entitlement fulfillment for %d payments", processed)


# ---------------------------------------------------------------------------
# Recurring coach payouts (cohort-scoped, block-based)
# ---------------------------------------------------------------------------


def _period_label(block_start: datetime, block_end: datetime, block_index: int) -> str:
    """Human-readable label for the block, e.g. "Block 1 — Apr 18 to May 16"."""
    fmt = "%b %d"
    return (
        f"Block {block_index + 1} — "
        f"{block_start.strftime(fmt)} to {block_end.strftime(fmt)}"
    )


async def process_recurring_payouts() -> None:
    """Daily cron: find recurring configs whose next_run_date has arrived,
    compute the block payout, insert a PENDING CoachPayout, persist any
    new make-up obligations, and advance the schedule.

    Idempotency:
      - Each config has block_index incremented after a successful run.
      - Late-join obligations are de-duplicated by (cohort, student,
        original_session_id, reason) before insert.
      - If the same task fires twice on the same day, the second call sees
        next_run_date already advanced and skips.
    """
    now = utc_now()
    processed = 0
    failed = 0

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(RecurringPayoutConfig).where(
                RecurringPayoutConfig.status == RecurringPayoutStatus.ACTIVE,
                RecurringPayoutConfig.next_run_date <= now,
            )
        )
        configs = list(result.scalars().all())

        for config in configs:
            try:
                # Don't process beyond the configured total.
                if config.block_index >= config.total_blocks:
                    config.status = RecurringPayoutStatus.COMPLETED
                    db.add(config)
                    await db.commit()
                    continue

                computation = await compute_block_payout(
                    db, config, config.block_index
                )

                # Insert the PENDING payout row.
                payout = CoachPayout(
                    coach_member_id=config.coach_member_id,
                    period_start=computation.block_start,
                    period_end=computation.block_end,
                    period_label=_period_label(
                        computation.block_start,
                        computation.block_end,
                        config.block_index,
                    ),
                    academy_earnings=computation.total_kobo,
                    session_earnings=0,
                    other_earnings=0,
                    total_amount=computation.total_kobo,
                    currency=config.currency,
                    status=PayoutStatus.PENDING,
                    admin_notes=(
                        f"Auto-generated from recurring config {config.id}. "
                        f"Block {config.block_index + 1}/{config.total_blocks}. "
                        f"Band {config.band_percentage}% × cohort price "
                        f"{config.cohort_price_amount} kobo ÷ "
                        f"{config.total_blocks} blocks ÷ "
                        f"{computation.sessions_in_block} sessions = "
                        f"{computation.per_session_amount_kobo} kobo per "
                        f"student-session. {len(computation.lines)} students."
                    ),
                )
                db.add(payout)
                await db.flush()  # Need payout.id for makeup credit linking.

                # Mark which make-up obligations were credited in this payout.
                # Find COMPLETED make-ups in this block window without payout link.
                makeup_credit_result = await db.execute(
                    select(CohortMakeupObligation).where(
                        CohortMakeupObligation.cohort_id == config.cohort_id,
                        CohortMakeupObligation.coach_member_id
                        == config.coach_member_id,
                        CohortMakeupObligation.status == MakeupStatus.COMPLETED,
                        CohortMakeupObligation.completed_at
                        >= computation.block_start,
                        CohortMakeupObligation.completed_at
                        < computation.block_end,
                        CohortMakeupObligation.pay_credited_in_payout_id.is_(
                            None
                        ),
                    )
                )
                for obligation in makeup_credit_result.scalars().all():
                    obligation.pay_credited_in_payout_id = payout.id

                # Persist new make-up obligations (de-duped by uniqueness key).
                for new_obligation in computation.new_makeup_obligations:
                    existing = await db.execute(
                        select(CohortMakeupObligation).where(
                            CohortMakeupObligation.cohort_id
                            == new_obligation["cohort_id"],
                            CohortMakeupObligation.student_member_id
                            == new_obligation["student_member_id"],
                            CohortMakeupObligation.original_session_id
                            == new_obligation["original_session_id"],
                            CohortMakeupObligation.reason
                            == new_obligation["reason"],
                        )
                    )
                    if existing.scalar_one_or_none():
                        continue  # Already tracked.
                    db.add(CohortMakeupObligation(**new_obligation))

                # Advance the schedule.
                config.block_index += 1
                config.next_run_date = config.next_run_date + timedelta(
                    days=config.block_length_days
                )
                if config.block_index >= config.total_blocks:
                    config.status = RecurringPayoutStatus.COMPLETED

                db.add(config)
                await db.commit()
                processed += 1
                logger.info(
                    "Created PENDING payout %s for coach %s (block %d/%d, total %d kobo)",
                    payout.id,
                    config.coach_member_id,
                    config.block_index,
                    config.total_blocks,
                    computation.total_kobo,
                )
            except Exception as exc:  # noqa: BLE001
                await db.rollback()
                failed += 1
                logger.exception(
                    "Failed to process recurring payout config %s: %s",
                    config.id,
                    exc,
                )

    if processed or failed:
        logger.info(
            "Recurring payout sweep complete: processed=%d failed=%d",
            processed,
            failed,
        )


async def expire_overdue_makeups() -> None:
    """Daily sweeper: any PENDING / SCHEDULED make-up obligation past the
    cohort's end_date becomes EXPIRED. No pay is credited for expired
    obligations — that's the natural deadline pressure.
    """
    now = utc_now()
    expired = 0

    async with AsyncSessionLocal() as db:
        # We rely on RecurringPayoutConfig.cohort_end_date as the snapshot
        # of the cohort's end. A make-up is overdue when the cohort window
        # has closed and the obligation is still pending/scheduled.
        result = await db.execute(
            select(CohortMakeupObligation, RecurringPayoutConfig.cohort_end_date)
            .join(
                RecurringPayoutConfig,
                RecurringPayoutConfig.cohort_id
                == CohortMakeupObligation.cohort_id,
            )
            .where(
                CohortMakeupObligation.status.in_(
                    [MakeupStatus.PENDING, MakeupStatus.SCHEDULED]
                ),
                RecurringPayoutConfig.cohort_end_date < now,
            )
        )
        for obligation, _cohort_end in result.all():
            obligation.status = MakeupStatus.EXPIRED
            db.add(obligation)
            expired += 1

        if expired:
            await db.commit()
            logger.info("Expired %d overdue make-up obligations", expired)
