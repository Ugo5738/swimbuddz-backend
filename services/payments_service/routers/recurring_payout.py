"""Admin and coach routes for recurring coach payouts and make-up obligations.

Admin endpoints (under /admin/recurring-payouts):
  - POST   /                  Create a recurring config for (coach, cohort).
                              Snapshots cohort price + dates. Sets next_run_date
                              to (cohort_start + block_length_days), so the
                              first payout fires on cohort start + 4 weeks.
  - GET    /                  List configs (with filters).
  - GET    /{id}              Get one config.
  - GET    /{id}/preview      Compute next block's payout WITHOUT writing
                              anything. For admin verification before approving.
  - PATCH  /{id}              Update band_percentage / status / notes.
  - POST   /{id}/run-now      Force the next-block computation immediately.
                              Useful for first-block back-pay or testing.

Make-up endpoints (under /admin/cohort-makeups):
  - GET    /                  List obligations across cohorts (with filters).
  - PATCH  /{id}/schedule     Admin override to schedule a make-up to a
                              specific session (coach has the same op via
                              coach router; admin override doesn't require
                              coach ownership).
  - PATCH  /{id}/cancel       Admin cancels an obligation.

Coach endpoints (under /coach/me/cohort-makeups):
  - GET    /                  List the calling coach's own make-up obligations
                              (filtered by the coach_member_id stored on each row).
  - PATCH  /{id}/schedule     Coach links a queued obligation to one of their
                              cohort sessions. Verifies ownership before write.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from libs.auth.dependencies import get_current_user, require_admin
from libs.auth.models import AuthUser
from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from libs.common.service_client import get_member_by_auth_id
from libs.db.config import AsyncSessionLocal
from services.payments_service.models import (
    CoachPayout,
    CohortMakeupObligation,
    MakeupStatus,
    PayoutStatus,
    RecurringPayoutConfig,
    RecurringPayoutStatus,
)
from services.payments_service.schemas import (
    MakeupObligationListResponse,
    MakeupObligationResponse,
    MakeupScheduleRequest,
    PayoutPreviewLine,
    PayoutPreviewResponse,
    RecurringPayoutConfigCreate,
    RecurringPayoutConfigListResponse,
    RecurringPayoutConfigResponse,
    RecurringPayoutConfigUpdate,
)
from services.payments_service.services.payout_calculator import (
    block_window,
    compute_block_payout,
)
from sqlalchemy import func, select, text

logger = get_logger(__name__)

admin_router = APIRouter(
    prefix="/admin/recurring-payouts", tags=["admin-recurring-payouts"]
)
makeups_admin_router = APIRouter(
    prefix="/admin/cohort-makeups", tags=["admin-cohort-makeups"]
)
makeups_coach_router = APIRouter(
    prefix="/coach/me/cohort-makeups", tags=["coach-cohort-makeups"]
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _fetch_cohort_snapshot(
    db, cohort_id: uuid.UUID
) -> dict:
    """Fetch cohort fields needed to seed a recurring config snapshot.

    Reads the cohorts and programs tables directly (same DB). Returns:
      - start_date, end_date, total_blocks, block_length_days
      - cohort_price_amount (in kobo), currency
      - required_coach_grade (for sanity check / audit)
      - pay_band_min, pay_band_max (from complexity score)
    """
    row = (
        await db.execute(
            text(
                """
                SELECT
                    c.id, c.start_date, c.end_date, c.required_coach_grade,
                    COALESCE(c.price_override, p.price_amount) AS price_amount,
                    p.currency,
                    p.duration_weeks,
                    s.pay_band_min, s.pay_band_max
                FROM public.cohorts c
                JOIN public.programs p ON p.id = c.program_id
                LEFT JOIN public.cohort_complexity_scores s
                    ON s.cohort_id = c.id
                WHERE c.id = :cohort_id
                """
            ),
            {"cohort_id": cohort_id},
        )
    ).mappings().first()
    if not row:
        return {}
    return dict(row)


# ---------------------------------------------------------------------------
# Recurring config CRUD
# ---------------------------------------------------------------------------


@admin_router.post(
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
            created_by_member_id=uuid.UUID(admin_member_id) if admin_member_id else None,
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


@admin_router.get("/", response_model=RecurringPayoutConfigListResponse)
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
            count_stmt = count_stmt.where(
                RecurringPayoutConfig.cohort_id == cohort_id
            )
        if status_filter:
            stmt = stmt.where(RecurringPayoutConfig.status == status_filter)
            count_stmt = count_stmt.where(
                RecurringPayoutConfig.status == status_filter
            )

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


@admin_router.get("/{config_id}", response_model=RecurringPayoutConfigResponse)
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


@admin_router.patch("/{config_id}", response_model=RecurringPayoutConfigResponse)
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


@admin_router.get(
    "/{config_id}/preview", response_model=PayoutPreviewResponse
)
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


@admin_router.post(
    "/{config_id}/run-now", response_model=RecurringPayoutConfigResponse
)
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
            raise HTTPException(
                status_code=400, detail="Config is not active"
            )
        if config.block_index >= config.total_blocks:
            raise HTTPException(
                status_code=400, detail="All blocks already paid"
            )
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


# ---------------------------------------------------------------------------
# Make-up obligations — admin
# ---------------------------------------------------------------------------


@makeups_admin_router.get("/", response_model=MakeupObligationListResponse)
async def list_makeup_obligations(
    cohort_id: Optional[uuid.UUID] = None,
    coach_member_id: Optional[uuid.UUID] = None,
    student_member_id: Optional[uuid.UUID] = None,
    status_filter: Optional[MakeupStatus] = Query(default=None, alias="status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    _admin: AuthUser = Depends(require_admin),
):
    async with AsyncSessionLocal() as db:
        stmt = select(CohortMakeupObligation)
        count_stmt = select(func.count()).select_from(CohortMakeupObligation)
        if cohort_id:
            stmt = stmt.where(CohortMakeupObligation.cohort_id == cohort_id)
            count_stmt = count_stmt.where(
                CohortMakeupObligation.cohort_id == cohort_id
            )
        if coach_member_id:
            stmt = stmt.where(
                CohortMakeupObligation.coach_member_id == coach_member_id
            )
            count_stmt = count_stmt.where(
                CohortMakeupObligation.coach_member_id == coach_member_id
            )
        if student_member_id:
            stmt = stmt.where(
                CohortMakeupObligation.student_member_id == student_member_id
            )
            count_stmt = count_stmt.where(
                CohortMakeupObligation.student_member_id == student_member_id
            )
        if status_filter:
            stmt = stmt.where(CohortMakeupObligation.status == status_filter)
            count_stmt = count_stmt.where(
                CohortMakeupObligation.status == status_filter
            )

        total = (await db.execute(count_stmt)).scalar_one()
        result = await db.execute(
            stmt.order_by(CohortMakeupObligation.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        items = [
            MakeupObligationResponse.model_validate(row)
            for row in result.scalars().all()
        ]
        return MakeupObligationListResponse(items=items, total=total)


@makeups_admin_router.patch(
    "/{obligation_id}/schedule", response_model=MakeupObligationResponse
)
async def admin_schedule_makeup(
    obligation_id: uuid.UUID,
    payload: MakeupScheduleRequest,
    _admin: AuthUser = Depends(require_admin),
):
    """Admin override to schedule a make-up to a specific session.

    Coaches use a separate coach-facing endpoint for the same operation
    on their own cohorts.
    """
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(CohortMakeupObligation).where(
                CohortMakeupObligation.id == obligation_id
            )
        )
        obligation = result.scalar_one_or_none()
        if not obligation:
            raise HTTPException(status_code=404, detail="Obligation not found")
        if obligation.status not in (MakeupStatus.PENDING, MakeupStatus.SCHEDULED):
            raise HTTPException(
                status_code=400,
                detail=f"Cannot reschedule obligation in status {obligation.status.value}",
            )

        obligation.scheduled_session_id = payload.scheduled_session_id
        obligation.status = MakeupStatus.SCHEDULED
        if payload.notes:
            obligation.notes = payload.notes
        await db.commit()
        await db.refresh(obligation)
        return MakeupObligationResponse.model_validate(obligation)


@makeups_admin_router.patch(
    "/{obligation_id}/cancel", response_model=MakeupObligationResponse
)
async def admin_cancel_makeup(
    obligation_id: uuid.UUID,
    _admin: AuthUser = Depends(require_admin),
):
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(CohortMakeupObligation).where(
                CohortMakeupObligation.id == obligation_id
            )
        )
        obligation = result.scalar_one_or_none()
        if not obligation:
            raise HTTPException(status_code=404, detail="Obligation not found")
        if obligation.status == MakeupStatus.COMPLETED:
            raise HTTPException(
                status_code=400, detail="Cannot cancel a completed make-up"
            )
        obligation.status = MakeupStatus.CANCELLED
        await db.commit()
        await db.refresh(obligation)
        return MakeupObligationResponse.model_validate(obligation)


# ---------------------------------------------------------------------------
# Coach-facing make-up endpoints
# ---------------------------------------------------------------------------


async def _resolve_coach_member_id(current_user: AuthUser) -> uuid.UUID:
    """Resolve the calling user's member_id, requiring the coach role."""
    if not current_user.has_role("coach"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Coach role required",
        )
    member = await get_member_by_auth_id(
        current_user.user_id, calling_service="payments"
    )
    if not member:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Member profile not found",
        )
    return uuid.UUID(member["id"])


@makeups_coach_router.get("/", response_model=MakeupObligationListResponse)
async def coach_list_makeup_obligations(
    cohort_id: Optional[uuid.UUID] = None,
    status_filter: Optional[MakeupStatus] = Query(default=None, alias="status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current_user: AuthUser = Depends(get_current_user),
):
    """List make-up obligations owned by the calling coach.

    Filtered server-side by `coach_member_id == current_user.member_id`,
    so coaches can never see another coach's obligations.
    """
    coach_member_id = await _resolve_coach_member_id(current_user)

    async with AsyncSessionLocal() as db:
        stmt = select(CohortMakeupObligation).where(
            CohortMakeupObligation.coach_member_id == coach_member_id,
        )
        count_stmt = select(func.count()).select_from(
            CohortMakeupObligation
        ).where(CohortMakeupObligation.coach_member_id == coach_member_id)
        if cohort_id:
            stmt = stmt.where(CohortMakeupObligation.cohort_id == cohort_id)
            count_stmt = count_stmt.where(
                CohortMakeupObligation.cohort_id == cohort_id
            )
        if status_filter:
            stmt = stmt.where(CohortMakeupObligation.status == status_filter)
            count_stmt = count_stmt.where(
                CohortMakeupObligation.status == status_filter
            )

        total = (await db.execute(count_stmt)).scalar_one()
        result = await db.execute(
            stmt.order_by(CohortMakeupObligation.created_at.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        items = [
            MakeupObligationResponse.model_validate(row)
            for row in result.scalars().all()
        ]
        return MakeupObligationListResponse(items=items, total=total)


@makeups_coach_router.patch(
    "/{obligation_id}/schedule", response_model=MakeupObligationResponse
)
async def coach_schedule_makeup(
    obligation_id: uuid.UUID,
    payload: MakeupScheduleRequest,
    current_user: AuthUser = Depends(get_current_user),
):
    """Coach links a queued obligation to one of their cohort sessions.

    Verifications:
      - Caller is the coach who owns the obligation.
      - The target session belongs to the same cohort as the obligation.
      - The session has not yet started (can't schedule a make-up to the past).
      - The obligation is in PENDING or SCHEDULED state (allows reschedule
        before the make-up is delivered).
    """
    coach_member_id = await _resolve_coach_member_id(current_user)

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(CohortMakeupObligation).where(
                CohortMakeupObligation.id == obligation_id
            )
        )
        obligation = result.scalar_one_or_none()
        if not obligation:
            raise HTTPException(status_code=404, detail="Obligation not found")
        if obligation.coach_member_id != coach_member_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not your obligation",
            )
        if obligation.status not in (MakeupStatus.PENDING, MakeupStatus.SCHEDULED):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Cannot schedule obligation in status "
                    f"{obligation.status.value}"
                ),
            )

        # Verify the target session is in the same cohort and is in the future.
        session_row = (
            await db.execute(
                text(
                    """
                    SELECT id, cohort_id, starts_at, status
                    FROM public.sessions
                    WHERE id = :sid
                    """
                ),
                {"sid": payload.scheduled_session_id},
            )
        ).mappings().first()
        if not session_row:
            raise HTTPException(
                status_code=404, detail="Target session not found"
            )
        if session_row["cohort_id"] != obligation.cohort_id:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Target session belongs to a different cohort than the "
                    "obligation"
                ),
            )
        starts_at = session_row["starts_at"]
        if starts_at is not None and starts_at <= utc_now():
            raise HTTPException(
                status_code=400,
                detail=(
                    "Target session has already started; pick a future session"
                ),
            )

        obligation.scheduled_session_id = payload.scheduled_session_id
        obligation.status = MakeupStatus.SCHEDULED
        if payload.notes:
            obligation.notes = payload.notes
        await db.commit()
        await db.refresh(obligation)
        return MakeupObligationResponse.model_validate(obligation)
