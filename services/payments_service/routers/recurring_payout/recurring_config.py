"""Recurring-payout config CRUD + preview + run-now (admin only)."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from libs.common.service_client import get_member_by_auth_id
from libs.db.config import AsyncSessionLocal
from services.payments_service.models import (
    RecurringPayoutConfig,
    RecurringPayoutStatus,
)
from services.payments_service.schemas import (
    PayoutPreviewLine,
    PayoutPreviewResponse,
    RecurringPayoutConfigCreate,
    RecurringPayoutConfigListResponse,
    RecurringPayoutConfigResponse,
    RecurringPayoutConfigUpdate,
)
from services.payments_service.services.payout_calculator import compute_block_payout
from sqlalchemy import func, select

from ._helpers import _fetch_cohort_snapshot

logger = get_logger(__name__)
router = APIRouter()


@router.post(
    "/",
    response_model=RecurringPayoutConfigResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_recurring_payout_config(
    payload: RecurringPayoutConfigCreate,
    current_user: AuthUser = Depends(require_admin),
):
    """Create a recurring payout config for a (coach, cohort) pair.

    Snapshots cohort price + start/end dates onto the config so payouts
    stay stable if cohort fields are later edited. The first run is
    scheduled for `cohort.start_date + block_length_days` (i.e. end of
    block 1 — pay-on-completion of work).
    """
    async with AsyncSessionLocal() as db:
        # Pre-flight: cohort exists & has been complexity-scored.
        cohort = await _fetch_cohort_snapshot(db, payload.cohort_id)
        if not cohort:
            raise HTTPException(status_code=404, detail="Cohort not found")
        if not cohort.get("end_date") or not cohort.get("start_date"):
            raise HTTPException(
                status_code=400, detail="Cohort missing start/end dates"
            )
        if cohort.get("pay_band_min") is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Cohort has no complexity score yet. Score the cohort "
                    "first via /admin/academy/cohorts/{id}/score so the band "
                    "percentage can be validated."
                ),
            )
        # Validate band % against cohort's pay band.
        band = float(payload.band_percentage)
        if not (cohort["pay_band_min"] <= band <= cohort["pay_band_max"]):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"band_percentage {band} outside cohort's pay band "
                    f"{cohort['pay_band_min']}-{cohort['pay_band_max']}%"
                ),
            )

        # Reject duplicates.
        existing = await db.execute(
            select(RecurringPayoutConfig).where(
                RecurringPayoutConfig.coach_member_id == payload.coach_member_id,
                RecurringPayoutConfig.cohort_id == payload.cohort_id,
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=409,
                detail="Recurring payout config already exists for this coach+cohort",
            )

        # Derive block plan from program duration. duration_weeks ÷ 4.
        duration_weeks = int(cohort.get("duration_weeks") or 0)
        if duration_weeks <= 0:
            raise HTTPException(
                status_code=400,
                detail="Program has no duration_weeks; cannot derive block count",
            )
        block_length_days = 28
        total_blocks = max(1, duration_weeks // 4)

        # First run = end of block 1 (cohort_start + 4 weeks).
        first_run = cohort["start_date"] + timedelta(days=block_length_days)

        # Resolve admin's member_id for audit.
        admin_member = await get_member_by_auth_id(
            current_user.user_id, calling_service="payments"
        )
        admin_member_id = admin_member["id"] if admin_member else None

        config = RecurringPayoutConfig(
            coach_member_id=payload.coach_member_id,
            cohort_id=payload.cohort_id,
            band_percentage=payload.band_percentage,
            total_blocks=total_blocks,
            block_length_days=block_length_days,
            cohort_start_date=cohort["start_date"],
            cohort_end_date=cohort["end_date"],
            cohort_price_amount=int(cohort["price_amount"] or 0),
            currency=cohort.get("currency") or "NGN",
            block_index=0,
            next_run_date=first_run,
            status=RecurringPayoutStatus.ACTIVE,
            created_by_member_id=uuid.UUID(admin_member_id)
            if admin_member_id
            else None,
            notes=payload.notes,
        )
        db.add(config)
        await db.commit()
        await db.refresh(config)

        logger.info(
            "Created recurring payout config %s coach=%s cohort=%s band=%s%% blocks=%d",
            config.id,
            config.coach_member_id,
            config.cohort_id,
            config.band_percentage,
            config.total_blocks,
        )
        return RecurringPayoutConfigResponse.model_validate(config)


@router.get("/", response_model=RecurringPayoutConfigListResponse)
async def list_recurring_payout_configs(
    coach_member_id: Optional[uuid.UUID] = None,
    cohort_id: Optional[uuid.UUID] = None,
    status_filter: Optional[RecurringPayoutStatus] = Query(
        default=None, alias="status"
    ),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _admin: AuthUser = Depends(require_admin),
):
    async with AsyncSessionLocal() as db:
        stmt = select(RecurringPayoutConfig)
        count_stmt = select(func.count()).select_from(RecurringPayoutConfig)
        if coach_member_id:
            stmt = stmt.where(RecurringPayoutConfig.coach_member_id == coach_member_id)
            count_stmt = count_stmt.where(
                RecurringPayoutConfig.coach_member_id == coach_member_id
            )
        if cohort_id:
            stmt = stmt.where(RecurringPayoutConfig.cohort_id == cohort_id)
            count_stmt = count_stmt.where(RecurringPayoutConfig.cohort_id == cohort_id)
        if status_filter:
            stmt = stmt.where(RecurringPayoutConfig.status == status_filter)
            count_stmt = count_stmt.where(RecurringPayoutConfig.status == status_filter)

        total = (await db.execute(count_stmt)).scalar_one()
        result = await db.execute(
            stmt.order_by(RecurringPayoutConfig.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        items = [
            RecurringPayoutConfigResponse.model_validate(row)
            for row in result.scalars().all()
        ]
        return RecurringPayoutConfigListResponse(items=items, total=total)


@router.get("/{config_id}", response_model=RecurringPayoutConfigResponse)
async def get_recurring_payout_config(
    config_id: uuid.UUID,
    _admin: AuthUser = Depends(require_admin),
):
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(RecurringPayoutConfig).where(RecurringPayoutConfig.id == config_id)
        )
        config = result.scalar_one_or_none()
        if not config:
            raise HTTPException(status_code=404, detail="Config not found")
        return RecurringPayoutConfigResponse.model_validate(config)


@router.patch("/{config_id}", response_model=RecurringPayoutConfigResponse)
async def update_recurring_payout_config(
    config_id: uuid.UUID,
    payload: RecurringPayoutConfigUpdate,
    _admin: AuthUser = Depends(require_admin),
):
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(RecurringPayoutConfig).where(RecurringPayoutConfig.id == config_id)
        )
        config = result.scalar_one_or_none()
        if not config:
            raise HTTPException(status_code=404, detail="Config not found")

        if payload.band_percentage is not None:
            # Re-validate against cohort's pay band.
            cohort = await _fetch_cohort_snapshot(db, config.cohort_id)
            if cohort and cohort.get("pay_band_min") is not None:
                band = float(payload.band_percentage)
                if not (cohort["pay_band_min"] <= band <= cohort["pay_band_max"]):
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"band_percentage {band} outside cohort's pay band "
                            f"{cohort['pay_band_min']}-{cohort['pay_band_max']}%"
                        ),
                    )
            config.band_percentage = payload.band_percentage

        if payload.status is not None:
            config.status = payload.status

        if payload.notes is not None:
            config.notes = payload.notes

        await db.commit()
        await db.refresh(config)
        return RecurringPayoutConfigResponse.model_validate(config)


@router.get("/{config_id}/preview", response_model=PayoutPreviewResponse)
async def preview_recurring_payout(
    config_id: uuid.UUID,
    _admin: AuthUser = Depends(require_admin),
):
    """Dry-run the next block payout WITHOUT writing anything.

    Returns the per-student breakdown and total. Useful for showing the
    admin exactly what the cron will produce when next_run_date arrives.
    """
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(RecurringPayoutConfig).where(RecurringPayoutConfig.id == config_id)
        )
        config = result.scalar_one_or_none()
        if not config:
            raise HTTPException(status_code=404, detail="Config not found")
        if config.block_index >= config.total_blocks:
            raise HTTPException(
                status_code=400,
                detail="All blocks already paid; preview not available",
            )

        computation = await compute_block_payout(db, config, config.block_index)
        return PayoutPreviewResponse(
            config_id=config.id,
            block_index=computation.block_index,
            block_start=computation.block_start,
            block_end=computation.block_end,
            per_session_amount_kobo=computation.per_session_amount_kobo,
            lines=[
                PayoutPreviewLine(
                    student_member_id=line.student_member_id,
                    student_name=line.student_name,
                    enrolled_at=line.enrolled_at,
                    sessions_in_block=line.sessions_in_block,
                    sessions_delivered=line.sessions_delivered,
                    sessions_excused=line.sessions_excused,
                    makeups_completed_in_block=line.makeups_completed,
                    per_session_amount_kobo=line.per_session_amount_kobo,
                    student_total_kobo=line.student_total_kobo,
                )
                for line in computation.lines
            ],
            total_kobo=computation.total_kobo,
            currency=config.currency,
        )


@router.post("/{config_id}/run-now", response_model=RecurringPayoutConfigResponse)
async def run_recurring_payout_now(
    config_id: uuid.UUID,
    _admin: AuthUser = Depends(require_admin),
):
    """Force-run the next block computation regardless of next_run_date.

    Use cases: first-block back-pay (manual trigger after creating the config),
    bug recovery, or admin testing. Idempotent — only runs the *next* block
    in sequence; never replays past blocks.
    """
    from services.payments_service.tasks import process_recurring_payouts

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(RecurringPayoutConfig).where(RecurringPayoutConfig.id == config_id)
        )
        config = result.scalar_one_or_none()
        if not config:
            raise HTTPException(status_code=404, detail="Config not found")
        if config.status != RecurringPayoutStatus.ACTIVE:
            raise HTTPException(status_code=400, detail="Config is not active")
        if config.block_index >= config.total_blocks:
            raise HTTPException(status_code=400, detail="All blocks already paid")
        # Pull next_run_date forward to now so the standard sweep picks it up.
        config.next_run_date = utc_now()
        await db.commit()

    # Run the sweep synchronously (it's a single-config invocation now).
    await process_recurring_payouts()

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(RecurringPayoutConfig).where(RecurringPayoutConfig.id == config_id)
        )
        config = result.scalar_one_or_none()
        return RecurringPayoutConfigResponse.model_validate(config)
