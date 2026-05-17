"""Coach-facing earnings summary (forward + backward looking)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends
from libs.auth.dependencies import get_current_user
from libs.auth.models import AuthUser
from libs.common.logging import get_logger
from libs.db.config import AsyncSessionLocal
from pydantic import BaseModel
from services.payments_service.models import (
    CoachPayout,
    PayoutStatus,
    RecurringPayoutConfig,
    RecurringPayoutStatus,
)
from services.payments_service.services.payout_calculator import compute_block_payout
from sqlalchemy import bindparam, func, select, text

from ._helpers import _resolve_coach_member_id

logger = get_logger(__name__)
router = APIRouter()


class CoachLineItem(BaseModel):
    """Per-student contribution to an upcoming block payout — the same data
    surfaced to admins on the preview, exposed to the coach so they can
    audit how their pay was computed."""

    student_member_id: uuid.UUID
    student_name: Optional[str] = None
    sessions_delivered: int  # default-present + LATE + ABSENT
    sessions_excused: int  # excused absences (will become make-ups)
    makeups_completed_in_block: int
    subtotal_kobo: int  # (delivered + makeups_completed) × per_session_rate


class CoachUpcomingPayout(BaseModel):
    """One row per active recurring config — what the coach will earn at
    the next block close based on what's been delivered so far.

    Includes the inputs to the formula so the coach can see exactly why
    the headline number is what it is:

        per_session_per_student =
            cohort_price_amount × band_percentage
            ÷ total_blocks ÷ sessions_in_block

        expected_amount_kobo =
            Σ over students( (delivered + makeups_completed) × per_session_rate )
    """

    config_id: uuid.UUID
    cohort_id: uuid.UUID
    cohort_name: Optional[str] = None
    band_percentage: Decimal
    block_index: int  # blocks already paid out
    total_blocks: int
    next_block_index: int  # the upcoming block (display 1-based as block_index + 1)
    block_start: datetime
    block_end: datetime
    next_run_date: datetime
    expected_amount_kobo: int
    sessions_in_block: int
    students_count: int
    # Formula inputs — let the frontend reconstruct the computation visibly
    cohort_price_amount: int  # kobo per student
    per_session_amount_kobo: (
        int  # cohort_price × band / total_blocks / sessions_in_block
    )
    lines: List[CoachLineItem]


class CoachEarningsSummaryResponse(BaseModel):
    """Snapshot of a coach's payout situation across all their cohorts."""

    coach_member_id: uuid.UUID
    currency: str = "NGN"

    # Lifetime aggregates
    total_paid_kobo: int  # sum of CoachPayout.total_amount where status=PAID
    total_pending_kobo: int  # PENDING + APPROVED + PROCESSING (in flight)

    # Forward-looking
    upcoming_payouts: List[CoachUpcomingPayout]
    upcoming_total_kobo: int  # sum across all active configs (next block only)

    # Recent history
    recent_payouts: List[dict]  # latest 5 CoachPayouts (any status)


@router.get("/", response_model=CoachEarningsSummaryResponse)
async def coach_earnings_summary(
    current_user: AuthUser = Depends(get_current_user),
):
    """Forward + backward looking earnings view for the calling coach.

    Forward: for each ACTIVE recurring config the coach owns, compute the
    upcoming block payout (uses the same calculator the cron does, so the
    number matches what would actually be created when next_run_date hits).

    Backward: lifetime PAID totals + currently-in-flight totals + recent
    5 payout rows.

    Auth: any logged-in coach (filters server-side by their member_id).
    """
    coach_member_id = await _resolve_coach_member_id(current_user)

    async with AsyncSessionLocal() as db:
        # 1. Lifetime aggregates from coach_payouts.
        paid_sum = (
            await db.execute(
                select(func.coalesce(func.sum(CoachPayout.total_amount), 0)).where(
                    CoachPayout.coach_member_id == coach_member_id,
                    CoachPayout.status == PayoutStatus.PAID,
                )
            )
        ).scalar_one()
        pending_sum = (
            await db.execute(
                select(func.coalesce(func.sum(CoachPayout.total_amount), 0)).where(
                    CoachPayout.coach_member_id == coach_member_id,
                    CoachPayout.status.in_(
                        [
                            PayoutStatus.PENDING,
                            PayoutStatus.APPROVED,
                            PayoutStatus.PROCESSING,
                        ]
                    ),
                )
            )
        ).scalar_one()

        # 2. Active recurring configs.
        configs_result = await db.execute(
            select(RecurringPayoutConfig).where(
                RecurringPayoutConfig.coach_member_id == coach_member_id,
                RecurringPayoutConfig.status == RecurringPayoutStatus.ACTIVE,
            )
        )
        active_configs = list(configs_result.scalars().all())

        # 3. Per-config upcoming preview. Cohort name from cohorts table.
        cohort_ids = [c.cohort_id for c in active_configs]
        cohort_names: dict = {}
        if cohort_ids:
            name_rows = (
                (
                    await db.execute(
                        text(
                            "SELECT id, name FROM public.cohorts WHERE id IN :ids"
                        ).bindparams(bindparam("ids", expanding=True)),
                        {"ids": cohort_ids},
                    )
                )
                .mappings()
                .all()
            )
            cohort_names = {r["id"]: r["name"] for r in name_rows}

        upcoming: list[CoachUpcomingPayout] = []
        upcoming_total = 0
        for config in active_configs:
            if config.block_index >= config.total_blocks:
                continue
            try:
                computation = await compute_block_payout(db, config, config.block_index)
            except Exception:
                logger.exception(
                    "Failed to compute upcoming payout for config %s; skipping",
                    config.id,
                )
                continue
            upcoming.append(
                CoachUpcomingPayout(
                    config_id=config.id,
                    cohort_id=config.cohort_id,
                    cohort_name=cohort_names.get(config.cohort_id),
                    band_percentage=config.band_percentage,
                    block_index=config.block_index,
                    total_blocks=config.total_blocks,
                    next_block_index=config.block_index,
                    block_start=computation.block_start,
                    block_end=computation.block_end,
                    next_run_date=config.next_run_date,
                    expected_amount_kobo=computation.total_kobo,
                    sessions_in_block=computation.sessions_in_block,
                    students_count=len(computation.lines),
                    cohort_price_amount=config.cohort_price_amount,
                    per_session_amount_kobo=computation.per_session_amount_kobo,
                    lines=[
                        CoachLineItem(
                            student_member_id=ln.student_member_id,
                            student_name=ln.student_name,
                            sessions_delivered=ln.sessions_delivered,
                            sessions_excused=ln.sessions_excused,
                            makeups_completed_in_block=ln.makeups_completed,
                            subtotal_kobo=ln.student_total_kobo,
                        )
                        for ln in computation.lines
                    ],
                )
            )
            upcoming_total += computation.total_kobo

        # 4. Recent 5 payouts (any status) for the history strip.
        recent_rows = (
            (
                await db.execute(
                    select(CoachPayout)
                    .where(CoachPayout.coach_member_id == coach_member_id)
                    .order_by(CoachPayout.created_at.desc())
                    .limit(5)
                )
            )
            .scalars()
            .all()
        )
        recent = [
            {
                "id": str(p.id),
                "period_label": p.period_label,
                "period_start": p.period_start.isoformat(),
                "period_end": p.period_end.isoformat(),
                "total_amount_kobo": p.total_amount,
                "status": p.status.value,
                "paid_at": p.paid_at.isoformat() if p.paid_at else None,
                "created_at": p.created_at.isoformat(),
            }
            for p in recent_rows
        ]

        return CoachEarningsSummaryResponse(
            coach_member_id=coach_member_id,
            currency="NGN",
            total_paid_kobo=int(paid_sum or 0),
            total_pending_kobo=int(pending_sum or 0),
            upcoming_payouts=upcoming,
            upcoming_total_kobo=upcoming_total,
            recent_payouts=recent,
        )
