"""Stroke Lab admin endpoints — queue monitoring + reanalyze.

  GET  /admin/ai/analyze/queue              counts by status, recent jobs
  POST /admin/ai/analyze/reanalyze/{job_id} reset job + re-enqueue worker

Admin auth via libs.auth.dependencies.require_admin. Separate file from
the member router so the admin surface can evolve (rate limits, RBAC
splits, etc.) without bloating the member-facing module.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from arq import create_pool
from fastapi import APIRouter, Depends, HTTPException
from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.common.arq_config import get_redis_settings
from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from libs.db.session import get_async_db
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.ai_service.models import AnalysisJob, AnalysisJobStatus

logger = get_logger(__name__)

admin_router = APIRouter(prefix="/admin/analyze", tags=["stroke-lab-admin"])


# ── Response schemas ─────────────────────────────────────────────


class QueueStatusCounts(BaseModel):
    pending: int = 0
    processing: int = 0
    completed: int = 0
    failed: int = 0


class QueueRecentJob(BaseModel):
    id: uuid.UUID
    member_auth_id: uuid.UUID
    status: str
    stroke_type: str
    created_at: datetime
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None


class QueueSnapshot(BaseModel):
    """Snapshot the /admin/ai/queue page renders."""

    total_jobs: int
    counts: QueueStatusCounts
    counts_last_24h: QueueStatusCounts
    success_rate_pct: float = Field(
        description=(
            "Fraction of finished jobs (completed + failed) in the last 24h "
            "that completed successfully, ×100. Returns 0 when no finished "
            "jobs exist."
        )
    )
    recent_jobs: list[QueueRecentJob]
    queue_depth_approx: int = Field(
        description="Pending + processing — what the worker has to chew through."
    )


# ── Helpers ──────────────────────────────────────────────────────


async def _counts_in_window(
    db: AsyncSession, since: Optional[datetime] = None
) -> QueueStatusCounts:
    stmt = select(AnalysisJob.status, func.count(AnalysisJob.id))
    if since is not None:
        stmt = stmt.where(AnalysisJob.created_at >= since)
    stmt = stmt.group_by(AnalysisJob.status)
    rows = (await db.execute(stmt)).all()
    by_status = {row[0]: int(row[1]) for row in rows}

    # row[0] can be either the Enum instance or its value depending on
    # SQLAlchemy dialect handling — coerce both.
    def _get(key: AnalysisJobStatus) -> int:
        return int(by_status.get(key, by_status.get(key.value, 0)))

    return QueueStatusCounts(
        pending=_get(AnalysisJobStatus.PENDING),
        processing=_get(AnalysisJobStatus.PROCESSING),
        completed=_get(AnalysisJobStatus.COMPLETED),
        failed=_get(AnalysisJobStatus.FAILED),
    )


# ── GET /admin/analyze/queue ─────────────────────────────────────


@admin_router.get(
    "/queue",
    response_model=QueueSnapshot,
    summary="Stroke Lab queue health: counts by status, recent jobs, success rate",
)
async def queue_snapshot(
    recent_limit: int = 25,
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
) -> QueueSnapshot:
    if recent_limit <= 0 or recent_limit > 100:
        recent_limit = 25

    total = int(
        (await db.execute(select(func.count(AnalysisJob.id)))).scalar_one()
    )
    all_counts = await _counts_in_window(db)
    cutoff = utc_now() - timedelta(hours=24)
    day_counts = await _counts_in_window(db, since=cutoff)

    finished_24h = day_counts.completed + day_counts.failed
    success_rate = (
        round(day_counts.completed / finished_24h * 100, 1)
        if finished_24h > 0
        else 0.0
    )

    recent_rows = (
        await db.execute(
            select(AnalysisJob)
            .order_by(AnalysisJob.created_at.desc())
            .limit(recent_limit)
        )
    ).scalars().all()
    recent = [
        QueueRecentJob(
            id=row.id,
            member_auth_id=row.member_auth_id,
            status=row.status.value
            if hasattr(row.status, "value")
            else str(row.status),
            stroke_type=row.stroke_type,
            created_at=row.created_at,
            completed_at=row.completed_at,
            error_message=row.error_message,
        )
        for row in recent_rows
    ]

    return QueueSnapshot(
        total_jobs=total,
        counts=all_counts,
        counts_last_24h=day_counts,
        success_rate_pct=success_rate,
        recent_jobs=recent,
        queue_depth_approx=all_counts.pending + all_counts.processing,
    )


# ── POST /admin/analyze/reanalyze/{job_id} ───────────────────────


class ReanalyzeResponse(BaseModel):
    job_id: uuid.UUID
    status: str
    enqueued: bool


@admin_router.post(
    "/reanalyze/{job_id}",
    response_model=ReanalyzeResponse,
    summary="Reset a job to PENDING and re-enqueue the worker task",
)
async def reanalyze_job(
    job_id: uuid.UUID,
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
) -> ReanalyzeResponse:
    job = await db.get(AnalysisJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    if not job.video_storage_path:
        # If the original upload is gone (deleted, never set), there's
        # nothing for the worker to pull. Refuse rather than churn.
        raise HTTPException(
            status_code=409,
            detail="Job has no original video; cannot reanalyze.",
        )

    job.status = AnalysisJobStatus.PENDING
    job.started_at = None
    job.completed_at = None
    job.error_message = None
    # Old annotated artifact will be overwritten by the worker on success;
    # leave the storage path so the GET endpoint still returns something
    # while the new run is in flight.
    await db.commit()

    enqueued = False
    try:
        pool = await create_pool(get_redis_settings())
        await pool.enqueue_job(
            "task_analyze_swim_video",
            str(job_id),
            _queue_name="arq:ai",
        )
        await pool.close()
        enqueued = True
    except Exception as exc:
        # Row is back in PENDING; caller can hit this endpoint again
        # once Redis is reachable to trigger the worker.
        logger.exception(
            "Failed to enqueue reanalyze for %s: %s", job_id, exc
        )

    return ReanalyzeResponse(
        job_id=job_id,
        status=AnalysisJobStatus.PENDING.value,
        enqueued=enqueued,
    )
