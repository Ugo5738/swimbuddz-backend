"""Extend recurring coach-payout schedules for admin-billable cohort extensions."""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.payments_service.models import (
    RecurringPayoutConfig,
    RecurringPayoutStatus,
)


@dataclass(frozen=True)
class ExtendedPayoutSchedule:
    config_id: uuid.UUID
    previous_total_blocks: int
    total_blocks: int
    next_block_index: int
    next_run_date: datetime
    changed: bool


def extended_block_count(
    *,
    cohort_start: datetime,
    proposed_end: datetime,
    block_length_days: int,
) -> int:
    """Return the number of fixed-length payout blocks needed through end date."""
    if block_length_days <= 0:
        raise ValueError("block_length_days must be positive")
    duration = proposed_end - cohort_start
    if duration.total_seconds() <= 0:
        raise ValueError("proposed_end must be after cohort_start")
    block_seconds = timedelta(days=block_length_days).total_seconds()
    return max(1, math.ceil(duration.total_seconds() / block_seconds))


def next_unpaid_block_end(
    config: RecurringPayoutConfig, proposed_end: datetime
) -> datetime:
    """Return when the config's next unpaid (possibly partial) block closes."""
    block_end = config.cohort_start_date + timedelta(
        days=config.block_length_days * (config.block_index + 1)
    )
    final_close = proposed_end
    if (
        final_close.hour == 0
        and final_close.minute == 0
        and final_close.second == 0
        and final_close.microsecond == 0
    ):
        final_close += timedelta(days=1)
    return min(block_end, final_close) if block_end < proposed_end else final_close


async def extend_recurring_payout_schedules(
    db: AsyncSession,
    *,
    cohort_id: uuid.UUID,
    current_end: datetime,
    proposed_end: datetime,
) -> list[ExtendedPayoutSchedule]:
    """Add billable extension blocks to every recurring config for a cohort.

    The operation is idempotent. It preserves the frozen per-class rate and
    per-student class cap; only the payout window/schedule is extended.
    """
    if proposed_end <= current_end:
        raise ValueError("proposed_end must be after current_end")

    result = await db.execute(
        select(RecurringPayoutConfig)
        .where(RecurringPayoutConfig.cohort_id == cohort_id)
        .with_for_update()
    )
    configs = list(result.scalars().all())
    schedules: list[ExtendedPayoutSchedule] = []

    for config in configs:
        previous_total_blocks = config.total_blocks
        required_total_blocks = extended_block_count(
            cohort_start=config.cohort_start_date,
            proposed_end=proposed_end,
            block_length_days=config.block_length_days,
        )

        if (
            config.cohort_end_date >= proposed_end
            and config.total_blocks >= required_total_blocks
        ):
            schedules.append(
                ExtendedPayoutSchedule(
                    config_id=config.id,
                    previous_total_blocks=previous_total_blocks,
                    total_blocks=config.total_blocks,
                    next_block_index=config.block_index,
                    next_run_date=config.next_run_date,
                    changed=False,
                )
            )
            continue

        if config.cohort_end_date not in (current_end, proposed_end):
            raise ValueError(
                "Recurring payout snapshot end date does not match the "
                f"extension start for config {config.id}"
            )
        if config.per_class_amount_kobo is None or not config.total_classes:
            raise ValueError(f"Config {config.id} has no frozen per-class payout rate")

        total_blocks = required_total_blocks
        if total_blocks <= config.total_blocks:
            raise ValueError(
                f"Extension does not add a payout block for config {config.id}"
            )

        config.cohort_end_date = proposed_end
        config.total_blocks = total_blocks
        if config.block_index < total_blocks:
            config.status = RecurringPayoutStatus.ACTIVE
            # A config that was already complete has no useful upcoming run
            # date. Schedule the newly-added block for its actual close.
            if config.block_index >= previous_total_blocks:
                config.next_run_date = next_unpaid_block_end(config, proposed_end)

        audit_note = (
            f"Admin marked cohort extension {current_end.date()} to "
            f"{proposed_end.date()} as coach-payout billable."
        )
        config.notes = (
            f"{config.notes.rstrip()} | {audit_note}" if config.notes else audit_note
        )

        schedules.append(
            ExtendedPayoutSchedule(
                config_id=config.id,
                previous_total_blocks=previous_total_blocks,
                total_blocks=config.total_blocks,
                next_block_index=config.block_index,
                next_run_date=config.next_run_date,
                changed=True,
            )
        )

    await db.commit()
    return schedules
