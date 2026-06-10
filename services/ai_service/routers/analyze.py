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
from typing import Annotated, Optional

from arq import create_pool
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from libs.auth.dependencies import get_current_user
from libs.auth.models import AuthUser
from libs.common.arq_config import get_redis_settings
from libs.common.logging import get_logger
from libs.db.session import get_async_db
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.ai_service.analysis.storage import (
    delete_job_assets,
    signed_url_for_annotated,
    signed_url_for_upload,
    upload_user_video,
)
from services.ai_service.models import (
    AnalysisJob,
    AnalysisJobStatus,
    AnalysisResult,
)
from services.ai_service.analysis.drills import resolve_drill
from services.ai_service.schemas.analysis import (
    AnalysisJobDetailResponse,
    AnalysisJobResponse,
    AnalysisResultPayload,
    DrillSuggestion,
    Observation,
    TrackingGap,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/analyze", tags=["stroke-lab"])

# v0 only supports freestyle. Reject anything else at the API edge so the
# worker never has to think about it.
SUPPORTED_STROKES = {"freestyle"}

# Hard upload limit per the design doc.
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_LIST_LIMIT = 50


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
    payload: Optional[AnalysisResultPayload] = None
    if result is not None:
        # Resolve each observation's drill_key into full drill copy from the
        # bank (the DB only stores the key, so swapping the bank doesn't need
        # a data migration).
        observations: list[Observation] = []
        for obs in result.observations or []:
            drill = resolve_drill(obs.get("drill_key"))
            observations.append(
                Observation(
                    key=obs.get("key", ""),
                    severity=obs.get("severity", "suggestion"),
                    title=obs.get("title", ""),
                    detail=obs.get("detail", ""),
                    timestamp_s=obs.get("timestamp_s"),
                    drill=DrillSuggestion(**drill) if drill else None,
                )
            )
        tracking_gaps = [
            TrackingGap(
                start_s=g.get("start_s", 0.0),
                end_s=g.get("end_s", 0.0),
                duration_s=g.get("duration_s", 0.0),
            )
            for g in (result.tracking_gaps or [])
        ]
        payload = AnalysisResultPayload(
            detected_stroke=result.detected_stroke,
            pose_detection_rate=result.pose_detection_rate,
            frames_total=result.frames_total,
            frames_with_pose=result.frames_with_pose,
            stroke_rate_spm=result.stroke_rate_spm,
            body_roll_proxy_degrees=result.body_roll_proxy_degrees,
            breath_count_left=result.breath_count_left,
            breath_count_right=result.breath_count_right,
            breath_balance_left_ratio=result.breath_balance_left_ratio,
            summary_text=result.summary_text,
            observations=observations,
            tracking_gaps=tracking_gaps,
        )
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


async def _enqueue_analysis(job_id: uuid.UUID) -> None:
    """Push the analysis task onto the AI queue. Created on every call
    rather than holding a pool because the API container may not have
    redis available in every environment — failure is logged but not
    bubbled (the row sits in PENDING and is retryable)."""
    try:
        pool = await create_pool(get_redis_settings())
        await pool.enqueue_job(
            "task_analyze_swim_video",
            str(job_id),
            _queue_name="arq:ai",
        )
        await pool.close()
    except Exception as exc:
        logger.exception(
            "Failed to enqueue Stroke Lab job %s: %s — left in PENDING", job_id, exc
        )


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
