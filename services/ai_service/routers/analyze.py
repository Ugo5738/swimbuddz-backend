"""Stroke Lab — swim-video analysis endpoints.

  POST   /ai/analyze              create job from a multipart video upload
  GET    /ai/analyze/{job_id}     poll job status / fetch result + signed URLs
  GET    /ai/analyze/me           list the caller's last N jobs
  DELETE /ai/analyze/{job_id}     delete job row + storage assets

All endpoints require an authenticated Supabase user. Job ownership is
enforced on every read/delete — admins use the dedicated /admin endpoints
in the next file (queue monitoring).
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Annotated, Optional

from arq import create_pool
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import get_current_user
from libs.auth.models import AuthUser
from libs.common.arq_config import get_redis_settings
from libs.common.logging import get_logger
from libs.db.session import get_async_db
from services.ai_service.analysis.storage import (
    delete_job_assets,
    signed_url_for_annotated,
    signed_url_for_upload,
    upload_user_video,
)
from services.ai_service.constants import MEMBER_QUEUE_NAME
from services.ai_service.models import AnalysisJob, AnalysisJobStatus, AnalysisResult
from services.ai_service.routers._common import (
    build_result_payload,
    parse_coach_context,
    sign_coach_evidence,
    sign_coach_share,
)
from services.ai_service.schemas.analysis import (
    AnalysisJobDetailResponse,
    AnalysisJobResponse,
    InspectRequest,
)
from services.ai_service.services.drilldown import (
    ensure_drilldown_unlocked,
    existing_inspect_finding,
    validate_inspect,
)
from services.ai_service.services.inspect_status import inspect_key, set_inspect_status

logger = get_logger(__name__)

router = APIRouter(prefix="/analyze", tags=["stroke-lab"])

# v0 only supports freestyle. Reject anything else at the API edge so the
# worker never has to think about it.
SUPPORTED_STROKES = {"freestyle"}

# Hard upload limit per the design doc.
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_LIST_LIMIT = 50
_ACTIVE_INSPECT_STATUSES = {"queued", "processing", "retrying"}


# ── Helpers ──────────────────────────────────────────────────────


def _job_to_lifecycle_response(job: AnalysisJob) -> AnalysisJobResponse:
    return AnalysisJobResponse(
        id=job.id,
        member_auth_id=job.member_auth_id,
        stroke_type=job.stroke_type,
        status=job.status.value if hasattr(job.status, "value") else str(job.status),
        error_message=job.error_message,
        is_public=bool(job.is_public),
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
    )


async def _build_detail_response(
    job: AnalysisJob,
    result: Optional[AnalysisResult],
    *,
    include_signed_urls: bool,
) -> AnalysisJobDetailResponse:
    payload = build_result_payload(result) if result is not None else None
    if payload is not None and result is not None:
        payload.coach_evidence_urls = await sign_coach_evidence(result)
        payload.coach_share_urls = await sign_coach_share(result)
    original_url = None
    annotated_url = None
    if include_signed_urls:
        try:
            if job.video_storage_path:
                original_url = await signed_url_for_upload(job.video_storage_path)
        except Exception as exc:
            logger.warning("Could not sign upload URL for job %s: %s", job.id, exc)
        try:
            if job.annotated_video_storage_path:
                annotated_url = await signed_url_for_annotated(
                    job.annotated_video_storage_path
                )
        except Exception as exc:
            logger.warning("Could not sign annotated URL for job %s: %s", job.id, exc)

    lifecycle = _job_to_lifecycle_response(job)
    return AnalysisJobDetailResponse(
        **lifecycle.model_dump(),
        result=payload,
        original_video_url=original_url,
        annotated_video_url=annotated_url,
    )


async def _enqueue_analysis(
    job_id: uuid.UUID,
    queue_name: str = MEMBER_QUEUE_NAME,
    *,
    raise_on_error: bool = False,
) -> None:
    """Push the analysis task onto an AI queue (member ``arq:ai`` by default,
    or the public ``arq:ai-public`` queue for guest jobs). Created on every
    call rather than holding a pool because the API container may not have
    redis available in every environment.

    Member callers swallow failures (the row sits in PENDING, retryable). Public
    callers pass ``raise_on_error=True``: a credit is already reserved, so the
    caller must catch the failure and refund it (design §4.1)."""
    try:
        pool = await create_pool(get_redis_settings())
        await pool.enqueue_job(
            "task_analyze_swim_video",
            str(job_id),
            _queue_name=queue_name,
        )
        await pool.close()
    except Exception as exc:
        logger.exception(
            "Failed to enqueue Stroke Lab job %s: %s — left in PENDING", job_id, exc
        )
        if raise_on_error:
            raise


async def _queue_depth(queue_name: str = MEMBER_QUEUE_NAME) -> int | None:
    """Best-effort ARQ queue depth for UX/backpressure hints."""
    pool = await create_pool(get_redis_settings())
    try:
        return int(await pool.zcard(queue_name))
    except Exception:
        logger.debug("Could not read ARQ queue depth for %s", queue_name, exc_info=True)
        return None
    finally:
        await pool.close()


async def _enqueue_inspect(
    job_id: uuid.UUID,
    aspect: str,
    instance_id: int,
    queue_name: str = MEMBER_QUEUE_NAME,
    *,
    attempt: int = 1,
    defer_by_seconds: float | None = None,
) -> None:
    """Push a per-stroke drilldown task onto an AI queue. Raises on failure so the
    caller can surface a 502 (no credit is charged for inspect today, so there's
    nothing to refund)."""
    pool = await create_pool(get_redis_settings())
    try:
        kwargs = {}
        if defer_by_seconds:
            kwargs["_defer_by"] = timedelta(seconds=defer_by_seconds)
        await pool.enqueue_job(
            "task_inspect_instance",
            str(job_id),
            aspect,
            instance_id,
            attempt,
            _queue_name=queue_name,
            **kwargs,
        )
    finally:
        await pool.close()


async def _start_inspect_job(
    db: AsyncSession,
    *,
    job_id: uuid.UUID,
    result_row: AnalysisResult,
    aspect: str,
    instance_id: int,
    queue_name: str,
) -> dict:
    """Persist visible inspect status, then enqueue the worker task."""
    key = inspect_key(aspect, instance_id)
    current = ((result_row.coach_result or {}).get("inspect_jobs") or {}).get(key)
    if current and current.get("status") in _ACTIVE_INSPECT_STATUSES:
        return {
            "status": current.get("status"),
            "inspect_status": current,
            "queue_depth": current.get("queue_depth"),
        }

    depth = await _queue_depth(queue_name)
    estimated_depth = depth + 1 if depth is not None else None
    payload = await set_inspect_status(
        db,
        job_id=job_id,
        aspect=aspect,
        instance_id=instance_id,
        status="queued",
        attempt=1,
        message="Queued for the video coach.",
        queue_depth=estimated_depth,
    )
    await db.commit()
    try:
        await _enqueue_inspect(job_id, aspect, instance_id, queue_name, attempt=1)
    except Exception:
        await set_inspect_status(
            db,
            job_id=job_id,
            aspect=aspect,
            instance_id=instance_id,
            status="failed",
            attempt=1,
            message="Could not queue the video coach. Try again.",
            error_reason="queue_failed",
        )
        await db.commit()
        raise
    return {
        "status": "queued",
        "inspect_status": payload,
        "queue_depth": estimated_depth,
    }


# ── POST /ai/analyze ─────────────────────────────────────────────


@router.post(
    "",
    response_model=AnalysisJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload a freestyle swim video and queue a Stroke Lab analysis",
)
async def create_analysis_job(
    video: Annotated[UploadFile, File(description="Swim video, ≤50 MB, ≤60s")],
    stroke_type: Annotated[str, Form()] = "freestyle",
    is_public: Annotated[bool, Form()] = False,
    discipline: Annotated[str, Form()] = "general",
    level: Annotated[str | None, Form()] = None,
    focus_area: Annotated[str | None, Form()] = None,
    goal_text: Annotated[str | None, Form()] = None,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> AnalysisJobResponse:
    """Create a new analysis job from an uploaded video.

    v0 limits: freestyle only, ≤50 MB body. Response is 202 ACCEPTED —
    the client polls GET /ai/analyze/{id} for status.
    """
    if stroke_type not in SUPPORTED_STROKES:
        raise HTTPException(
            status_code=400,
            detail=f"Only freestyle is supported in v0. Got: {stroke_type}",
        )

    # Read upload into memory. For 50 MB this is fine on the API
    # container; if we lift the cap we should switch to streamed
    # upload-via-presigned-URL.
    data = await video.read()
    if len(data) == 0:
        raise HTTPException(status_code=400, detail="Empty video upload")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Video exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit",
        )

    try:
        member_auth_id = uuid.UUID(current_user.user_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="Invalid auth user id") from exc

    job = AnalysisJob(
        member_auth_id=member_auth_id,
        stroke_type=stroke_type,
        video_storage_path="",  # filled after upload
        status=AnalysisJobStatus.PENDING,
        is_public=is_public,
        **parse_coach_context(discipline, level, focus_area, goal_text),
    )
    db.add(job)
    await db.flush()  # populates job.id

    suffix = "mp4"
    if video.filename and "." in video.filename:
        suffix = video.filename.rsplit(".", 1)[-1].lower()[:8] or "mp4"

    try:
        storage_path = await upload_user_video(
            member_auth_id,
            job.id,
            data,
            content_type=video.content_type or "video/mp4",
            suffix=suffix,
        )
    except Exception as exc:
        # Roll back the empty job row so PENDING doesn't accumulate.
        await db.rollback()
        logger.exception("Stroke Lab upload to storage failed: %s", exc)
        raise HTTPException(status_code=502, detail="Storage upload failed") from exc

    job.video_storage_path = storage_path
    await db.commit()
    await db.refresh(job)

    await _enqueue_analysis(job.id)
    return _job_to_lifecycle_response(job)


# ── GET /ai/analyze/me ───────────────────────────────────────────


@router.get(
    "/me",
    response_model=list[AnalysisJobResponse],
    summary="List the caller's most recent Stroke Lab jobs",
)
async def list_my_analyses(
    limit: int = 20,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> list[AnalysisJobResponse]:
    """Most recent first. Capped at MAX_LIST_LIMIT."""
    if limit <= 0 or limit > MAX_LIST_LIMIT:
        limit = MAX_LIST_LIMIT
    try:
        member_auth_id = uuid.UUID(current_user.user_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="Invalid auth user id") from exc

    stmt = (
        select(AnalysisJob)
        .where(AnalysisJob.member_auth_id == member_auth_id)
        .order_by(AnalysisJob.created_at.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    return [_job_to_lifecycle_response(row) for row in result.scalars().all()]


# ── GET /ai/analyze/{job_id} ─────────────────────────────────────


@router.get(
    "/{job_id}",
    response_model=AnalysisJobDetailResponse,
    summary="Fetch a Stroke Lab job's status + result + signed URLs",
)
async def get_analysis_job(
    job_id: uuid.UUID,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> AnalysisJobDetailResponse:
    job = await db.get(AnalysisJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    try:
        caller_id = uuid.UUID(current_user.user_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="Invalid auth user id") from exc

    is_owner = job.member_auth_id == caller_id
    if not is_owner and not job.is_public:
        # Same code as missing to avoid leaking job existence to non-owners.
        raise HTTPException(status_code=404, detail="Job not found")

    result_row = None
    if job.status == AnalysisJobStatus.COMPLETED:
        rs = await db.execute(
            select(AnalysisResult).where(AnalysisResult.job_id == job_id)
        )
        result_row = rs.scalar_one_or_none()

    return await _build_detail_response(
        job, result_row, include_signed_urls=is_owner or job.is_public
    )


# ── POST /ai/analyze/{job_id}/inspect (per-instance drilldown, §12.5) ────────


@router.post(
    "/{job_id}/inspect",
    summary="Coach one stored instance on demand (gated; 409 until unlocked)",
)
async def inspect_analysis(
    job_id: uuid.UUID,
    req: InspectRequest,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    job = await db.get(AnalysisJob, job_id)
    try:
        caller_id = uuid.UUID(current_user.user_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="Invalid auth user id") from exc
    if job is None or job.member_auth_id != caller_id:
        raise HTTPException(status_code=404, detail="Job not found")
    ensure_drilldown_unlocked()  # 409 while drilldown is gated off
    rs = await db.execute(select(AnalysisResult).where(AnalysisResult.job_id == job_id))
    result_row = rs.scalar_one_or_none()
    if result_row is None or not result_row.coach_result:
        raise HTTPException(status_code=404, detail="No analysis result to inspect")
    existing = existing_inspect_finding(result_row, req.aspect, req.instance_id)
    if existing is not None:
        return {"status": "ready", "finding": existing}  # already coached → $0
    validate_inspect(result_row, req.aspect, req.instance_id)
    try:
        return await _start_inspect_job(
            db,
            job_id=job_id,
            result_row=result_row,
            aspect=req.aspect,
            instance_id=req.instance_id,
            queue_name=MEMBER_QUEUE_NAME,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Could not queue inspect") from exc


# ── DELETE /ai/analyze/{job_id} ──────────────────────────────────


@router.delete(
    "/{job_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="Delete a Stroke Lab job and its storage assets",
)
async def delete_analysis_job(
    job_id: uuid.UUID,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> None:
    job = await db.get(AnalysisJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    try:
        caller_id = uuid.UUID(current_user.user_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="Invalid auth user id") from exc

    if job.member_auth_id != caller_id:
        raise HTTPException(status_code=404, detail="Job not found")

    uploaded_key = job.video_storage_path or None
    annotated_key = job.annotated_video_storage_path

    await db.delete(job)
    await db.commit()
    # Best-effort: storage cleanup happens after the DB row is gone so a
    # storage error doesn't leave the DB in an inconsistent state.
    await delete_job_assets(uploaded_key, annotated_key)
