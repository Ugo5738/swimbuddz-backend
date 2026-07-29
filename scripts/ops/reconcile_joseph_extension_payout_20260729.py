#!/usr/bin/env python3
"""Restore Joseph's omitted payout block for the approved cohort extension.

Dry-run by default. Use ``--apply`` to:

* extend the recurring payout snapshot from 2026-07-11 to 2026-08-08;
* add the fourth 28-day block created by the approved four-week extension;
* compute Block 4 with the production payout calculator; and
* create a PENDING payout only when the expected attendance reconciliation
  (Biyi 2, Jesudara 2, Winifred 1) totals NGN 25,000 as of 2026-07-29.

The payout remains linked to the recurring config, so the normal Recalculate
action can include any later Present/Late attendance through 2026-08-08.
No payout is approved or transferred by this script.
"""

from __future__ import annotations

import argparse
import asyncio
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from libs.db.config import AsyncSessionLocal
from services.payments_service.models import (
    CoachPayout,
    PayoutStatus,
    RecurringPayoutConfig,
    RecurringPayoutStatus,
)
from services.payments_service.services.ledger_emit import (
    emit_payout_accrual_to_ledger,
)
from services.payments_service.services.payout_calculator import (
    compute_block_payout,
)

CONFIG_ID = uuid.UUID("733ef9ec-27e7-41c7-b9bb-5865e135b9be")
COACH_ID = uuid.UUID("584168f1-26c3-4676-a476-5fd9ea033d1e")
COHORT_ID = uuid.UUID("fd868184-9e2f-46ad-a33b-9d0fdff54089")

OLD_END = datetime(2026, 7, 11, tzinfo=timezone.utc)
NEW_END = datetime(2026, 8, 8, tzinfo=timezone.utc)
PAYOUT_END = datetime(2026, 8, 9, tzinfo=timezone.utc)
EXTENSION_BLOCK_INDEX = 3
EXPECTED_TOTAL_KOBO = 2_500_000
EXPECTED_DELIVERED = {
    "Biyi Etti": 2,
    "Jesudara Hinmikaiye": 2,
    "Winifred Udoh": 1,
}


def _assert_original_config(config: RecurringPayoutConfig) -> None:
    expected = {
        "coach_member_id": COACH_ID,
        "cohort_id": COHORT_ID,
        "total_blocks": 3,
        "block_index": 3,
        "cohort_end_date": OLD_END,
        "total_classes": 12,
        "per_class_amount_kobo": 500_000,
    }
    for field, wanted in expected.items():
        actual = getattr(config, field)
        if actual != wanted:
            raise RuntimeError(
                f"Refusing reconciliation: config.{field}={actual!r}, "
                f"expected {wanted!r}"
            )
    if config.status != RecurringPayoutStatus.COMPLETED:
        raise RuntimeError("Refusing reconciliation: recurring config is not completed")


def _assert_computation(computation) -> None:
    delivered = {
        line.student_name: line.sessions_delivered
        for line in computation.lines
        if line.sessions_delivered
    }
    if delivered != EXPECTED_DELIVERED:
        raise RuntimeError(
            f"Refusing reconciliation: delivered classes are {delivered!r}, "
            f"expected {EXPECTED_DELIVERED!r}"
        )
    if computation.total_kobo != EXPECTED_TOTAL_KOBO:
        raise RuntimeError(
            f"Refusing reconciliation: total={computation.total_kobo}, "
            f"expected {EXPECTED_TOTAL_KOBO}"
        )
    if computation.block_start != OLD_END or computation.block_end != PAYOUT_END:
        raise RuntimeError(
            "Refusing reconciliation: extension block window is "
            f"{computation.block_start} to {computation.block_end}"
        )


async def reconcile(*, apply: bool) -> None:
    created_payout: CoachPayout | None = None
    async with AsyncSessionLocal() as db:
        existing = (
            await db.execute(
                select(CoachPayout).where(
                    CoachPayout.config_id == CONFIG_ID,
                    CoachPayout.block_index == EXTENSION_BLOCK_INDEX,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            print(
                "Already reconciled: "
                f"payout={existing.id} status={existing.status.value} "
                f"total_kobo={existing.total_amount}"
            )
            return

        config = (
            await db.execute(
                select(RecurringPayoutConfig)
                .where(RecurringPayoutConfig.id == CONFIG_ID)
                .with_for_update()
            )
        ).scalar_one()
        _assert_original_config(config)

        config.total_blocks = 4
        config.cohort_end_date = NEW_END
        config.status = RecurringPayoutStatus.ACTIVE

        computation = await compute_block_payout(db, config, EXTENSION_BLOCK_INDEX)
        _assert_computation(computation)

        created_payout = CoachPayout(
            coach_member_id=COACH_ID,
            config_id=CONFIG_ID,
            block_index=EXTENSION_BLOCK_INDEX,
            period_start=computation.block_start,
            period_end=computation.block_end,
            period_label="Block 4 — Jul 11 to Aug 08",
            academy_earnings=computation.total_kobo,
            session_earnings=0,
            other_earnings=0,
            total_amount=computation.total_kobo,
            currency=config.currency,
            status=PayoutStatus.PENDING,
            admin_notes=(
                "Reconciled 2026-07-29 after the approved four-week catch-up "
                "extension was omitted from recurring payout config "
                f"{CONFIG_ID}. 500000 kobo per attended student-class: "
                "Biyi 2, Jesudara 2, Winifred 1 = 2500000 kobo as of "
                "2026-07-29. Linked to Block 4 so Recalculate can include "
                "later Present/Late attendance through 2026-08-08."
            ),
        )
        db.add(created_payout)

        config.block_index = 4
        config.next_run_date = config.next_run_date + timedelta(days=28)
        config.status = RecurringPayoutStatus.COMPLETED
        audit_note = (
            "2026-07-29: added Block 4 for approved four-week catch-up "
            "extension through 2026-08-08."
        )
        config.notes = (
            f"{config.notes.rstrip()} | {audit_note}" if config.notes else audit_note
        )
        await db.flush()

        mode = "APPLY" if apply else "DRY RUN"
        print(f"Mode: {mode}")
        print(
            f"Block 4: {computation.block_start.isoformat()} -> "
            f"{computation.block_end.isoformat()}"
        )
        for name, delivered in EXPECTED_DELIVERED.items():
            print(f"  {name}: {delivered} attended class(es)")
        print(f"Pending total: {computation.total_kobo} kobo")
        print(f"Payout id: {created_payout.id}")

        if not apply:
            await db.rollback()
            print("Dry run complete; no changes written.")
            return

        await db.commit()
        await db.refresh(created_payout)
        await emit_payout_accrual_to_ledger(db, created_payout)
        print("Applied. Payout is PENDING; no approval or transfer was made.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    asyncio.run(reconcile(apply=args.apply))


if __name__ == "__main__":
    main()
