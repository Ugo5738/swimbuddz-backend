"""Admin-facing flywheel metrics endpoints.

These endpoints surface cross-service metrics that validate the
SwimBuddz ecosystem thesis (community→club→academy funnel + wallet
cross-service spend + cohort fill operational state).

Gateway routing: /api/v1/admin/reports/flywheel/{path} → /admin/reports/flywheel/{path}
"""

from datetime import timedelta
from typing import Optional

from arq.connections import ArqRedis, create_pool
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.common.arq_config import get_redis_settings
from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from libs.db.session import get_async_db
from services.reporting_service.models import (
    CohortFillSnapshot,
    FunnelConversionSnapshot,
    FunnelStage,
    WalletEcosystemSnapshot,
)
from services.reporting_service.schemas.flywheel import (
    CohortFillSnapshotResponse,
    FlywheelOverviewResponse,
    FunnelConversionSnapshotResponse,
    RefreshFlywheelResponse,
    WalletEcosystemSnapshotResponse,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/admin/reports/flywheel", tags=["admin-flywheel"])

# Cohort considered "at risk" if fill rate <50% within 4 weeks of start
AT_RISK_FILL_THRESHOLD = 0.5
AT_RISK_DAYS_UNTIL_START = 28


@router.get("/overview", response_model=FlywheelOverviewResponse)
async def flywheel_overview(
    admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Single-call dashboard overview combining all flywheel metrics.

    Returns the latest snapshot for each metric category. Marks ``is_stale``
    if no refresh has occurred in the last 36 hours.
    """
    cohorts = await _latest_cohort_snapshots(db)
    fill_avg = sum(c.fill_rate for c in cohorts) / len(cohorts) if cohorts else None
    open_cohorts = [c for c in cohorts if c.cohort_status in {"open", "active"}]
    at_risk = [
        c
        for c in open_cohorts
        if c.fill_rate < AT_RISK_FILL_THRESHOLD
        and (
            c.days_until_start is not None
            and c.days_until_start <= AT_RISK_DAYS_UNTIL_START
        )
    ]

    c2c = await _latest_funnel_snapshot(db, FunnelStage.COMMUNITY_TO_CLUB)
    c2a = await _latest_funnel_snapshot(db, FunnelStage.CLUB_TO_ACADEMY)
    wallet = await _latest_wallet_snapshot(db)

    last_refresh_candidates = [
        s.snapshot_taken_at
        for s in (cohorts[0] if cohorts else None, c2c, c2a, wallet)
        if s is not None
    ]
    last_refreshed = max(last_refresh_candidates) if last_refresh_candidates else None
    is_stale = last_refreshed is None or (utc_now() - last_refreshed) > timedelta(
        hours=36
    )

    return FlywheelOverviewResponse(
        cohort_fill_avg=fill_avg,
        open_cohorts_count=len(open_cohorts),
        open_cohorts_at_risk_count=len(at_risk),
        community_to_club_rate=c2c.conversion_rate if c2c else None,
        community_to_club_period=c2c.cohort_period if c2c else None,
        club_to_academy_rate=c2a.conversion_rate if c2a else None,
        club_to_academy_period=c2a.cohort_period if c2a else None,
        wallet_cross_service_rate=wallet.cross_service_rate if wallet else None,
        wallet_active_users=wallet.active_wallet_users if wallet else 0,
        last_refreshed_at=last_refreshed,
        is_stale=is_stale,
    )


@router.get("/cohorts", response_model=list[CohortFillSnapshotResponse])
async def flywheel_cohorts(
    status: str = Query("open,active", description="Comma-separated cohort statuses"),
    sort: str = Query(
        "fill_rate_asc",
        regex="^(fill_rate_asc|fill_rate_desc|starts_at_asc|starts_at_desc)$",
    ),
    admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Latest cohort fill snapshots, default sorted by fill rate ascending.

    Acting on the lowest-filled cohorts first is the operational use-case.
    """
    cohorts = await _latest_cohort_snapshots(db)
    statuses = {s.strip().lower() for s in status.split(",") if s.strip()}
    cohorts = [c for c in cohorts if c.cohort_status in statuses]

    if sort == "fill_rate_asc":
        cohorts.sort(key=lambda c: c.fill_rate)
    elif sort == "fill_rate_desc":
        cohorts.sort(key=lambda c: c.fill_rate, reverse=True)
    elif sort == "starts_at_asc":
        cohorts.sort(key=lambda c: c.starts_at or utc_now())
    elif sort == "starts_at_desc":
        cohorts.sort(key=lambda c: c.starts_at or utc_now(), reverse=True)

    return cohorts


@router.get("/funnel", response_model=list[FunnelConversionSnapshotResponse])
async def flywheel_funnel(
    funnel_stage: Optional[FunnelStage] = Query(None),
    cohort_period: Optional[str] = Query(None, description="e.g. 2026-Q1"),
    limit: int = Query(20, ge=1, le=100),
    admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Funnel conversion snapshots, filterable by stage and period."""
    stmt = select(FunnelConversionSnapshot)
    if funnel_stage is not None:
        stmt = stmt.where(FunnelConversionSnapshot.funnel_stage == funnel_stage)
    if cohort_period:
        stmt = stmt.where(FunnelConversionSnapshot.cohort_period == cohort_period)
    stmt = stmt.order_by(desc(FunnelConversionSnapshot.snapshot_taken_at)).limit(limit)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/wallet", response_model=Optional[WalletEcosystemSnapshotResponse])
async def flywheel_wallet(
    admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Most recent wallet ecosystem snapshot."""
    return await _latest_wallet_snapshot(db)


@router.post("/refresh", response_model=RefreshFlywheelResponse)
async def flywheel_refresh(
    admin: AuthUser = Depends(require_admin),
):
    """Trigger an async refresh of all flywheel snapshots.

    Enqueues the ``task_refresh_all_flywheel`` ARQ job. Returns immediately;
    poll ``/overview`` to see the new ``last_refreshed_at``.
    """
    try:
        redis: ArqRedis = await create_pool(get_redis_settings())
        await redis.enqueue_job(
            "task_refresh_all_flywheel", _queue_name="arq:reporting"
        )
        await redis.close()
    except Exception as e:
        logger.exception("flywheel_refresh: failed to enqueue job")
        raise HTTPException(status_code=500, detail=f"Failed to enqueue: {e}")

    return RefreshFlywheelResponse(
        job_enqueued=True,
        message="Flywheel snapshot refresh enqueued. Poll /overview in 1-2 minutes.",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers — latest snapshot per category
# ─────────────────────────────────────────────────────────────────────────────


async def _latest_cohort_snapshots(db: AsyncSession) -> list[CohortFillSnapshot]:
    """One row per cohort: the most recent snapshot for each.

    Uses DISTINCT ON for efficiency in Postgres.
    """
    # SQLAlchemy DISTINCT ON pattern: order by cohort_id then snapshot_taken_at DESC
    from sqlalchemy import func

    # Using a window-function style query: most recent snapshot per cohort
    subq = select(
        CohortFillSnapshot,
        func.row_number()
        .over(
            partition_by=CohortFillSnapshot.cohort_id,
            order_by=desc(CohortFillSnapshot.snapshot_taken_at),
        )
        .label("rn"),
    ).subquery()
    stmt = (
        select(CohortFillSnapshot)
        .join(subq, CohortFillSnapshot.id == subq.c.id)
        .where(subq.c.rn == 1)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def _latest_funnel_snapshot(
    db: AsyncSession, stage: FunnelStage
) -> Optional[FunnelConversionSnapshot]:
    stmt = (
        select(FunnelConversionSnapshot)
        .where(FunnelConversionSnapshot.funnel_stage == stage)
        .order_by(desc(FunnelConversionSnapshot.snapshot_taken_at))
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def _latest_wallet_snapshot(
    db: AsyncSession,
) -> Optional[WalletEcosystemSnapshot]:
    stmt = (
        select(WalletEcosystemSnapshot)
        .order_by(desc(WalletEcosystemSnapshot.snapshot_taken_at))
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()
