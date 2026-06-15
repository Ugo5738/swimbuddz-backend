"""Stroke Lab — PUBLIC (guest) analyzer endpoints.

  POST /ai/public/analyze            create a guest job from a multipart upload
  GET  /ai/public/analyze/{job_id}   poll status / fetch result + signed URLs

No Supabase auth. A guest is identified by their email plus a per-job
``guest_token`` (server-minted, returned on submit, presented on poll via the
``X-Guest-Token`` header or ``?guest_token`` query). Ownership mismatches return
404 (never 403) so we never leak which job_ids exist — mirroring the member
endpoint.

Phase 0 scope: enqueues to the existing ``arq:ai`` queue (compute isolation is
Phase 1), no credit/free-tier gate yet (Phase 2), no emailed magic-link yet
(Phase 3). See docs/design/STROKELAB_PUBLIC_ANALYZER_DESIGN.md.
"""

from __future__ import annotations

import re
import secrets
import uuid
from typing import Annotated, Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from libs.common.logging import get_logger
from libs.db.session import get_async_db
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.ai_service.analysis.storage import (
    delete_job_assets,
    signed_url_for_annotated,
    signed_url_for_upload,
    upload_guest_video,
)
from services.ai_service.constants import GUMROAD_CHECKOUT_BASE, PUBLIC_QUEUE_NAME
from services.ai_service.models import (
    AnalysisJob,
    AnalysisJobSource,
    AnalysisJobStatus,
    AnalysisResult,
)
from services.ai_service.routers._common import build_result_payload
from services.ai_service.routers.analyze import (
    MAX_UPLOAD_BYTES,
    SUPPORTED_STROKES,
    _enqueue_analysis,
)
from services.ai_service.schemas.analysis import (
    PublicAnalysisJobDetailResponse,
    PublicAnalysisJobResponse,
)
from services.ai_service.services.credit_ops import (
    NoCreditsError,
    acquire_for_submit,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/public", tags=["stroke-lab-public"])

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _normalize_email(raw: str) -> str:
    """Lowercase + basic-format-validate the guest email. Phase 0 only
    lowercases; full canonicalization (+tag / Gmail-dot stripping for the
    free-tier key) lands with credits in Phase 2."""
    email = (raw or "").strip().lower()
    if not email or len(email) > 320 or not _EMAIL_RE.match(email):
        raise HTTPException(status_code=422, detail="A valid email is required")
    return email


def _status_str(job: AnalysisJob) -> str:
    return job.status.value if hasattr(job.status, "value") else str(job.status)


# ── POST /ai/public/analyze ──────────────────────────────────────


@router.post(
    "/analyze",
    response_model=PublicAnalysisJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload a freestyle swim video as a guest and queue an analysis",
)
async def create_public_analysis_job(
    video: Annotated[UploadFile, File(description="Freestyle swim video, ≤50 MB")],
    guest_email: Annotated[str, Form()],
    stroke_type: Annotated[str, Form()] = "freestyle",
    db: AsyncSession = Depends(get_async_db),
) -> PublicAnalysisJobResponse:
    """Create a guest analysis job. Returns 202 — the client polls
    GET /ai/public/analyze/{id} (and, from Phase 3, gets an email)."""
    if stroke_type not in SUPPORTED_STROKES:
        raise HTTPException(
            status_code=400,
            detail=f"Only freestyle is supported. Got: {stroke_type}",
        )
    email = _normalize_email(guest_email)

    data = await video.read()
    if len(data) == 0:
        raise HTTPException(status_code=400, detail="Empty video upload")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Video exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit",
        )

    # Per-job bearer token — server-minted, never client-supplied.
    guest_token = secrets.token_urlsafe(32)

    job = AnalysisJob(
        member_auth_id=None,
        guest_email=email,
        guest_token=guest_token,
        source=AnalysisJobSource.PUBLIC,
        stroke_type=stroke_type,
        video_storage_path="",  # filled after upload
        status=AnalysisJobStatus.PENDING,
    )
    db.add(job)
    await db.flush()  # populates job.id

    suffix = "mp4"
    if video.filename and "." in video.filename:
        suffix = video.filename.rsplit(".", 1)[-1].lower()[:8] or "mp4"

    try:
        storage_path = await upload_guest_video(
            guest_token,
            job.id,
            data,
            content_type=video.content_type or "video/mp4",
            suffix=suffix,
        )
    except Exception as exc:
        await db.rollback()
        logger.exception("Public Stroke Lab upload to storage failed: %s", exc)
        raise HTTPException(status_code=502, detail="Storage upload failed") from exc

    job.video_storage_path = storage_path

    # Secure a credit (the free analysis or a purchased one) and RESERVE it under
    # the account lock, in THIS transaction — so the job + reserve commit
    # atomically. Reserve only AFTER the upload succeeded, so an upload failure
    # never leaves a dangling reservation (design §4.1).
    try:
        reserve_entry = await acquire_for_submit(db, raw_email=email, job_id=job.id)
    except NoCreditsError:
        await db.rollback()  # the job row never persists — no orphan job
        await delete_job_assets(storage_path, None)  # best-effort: drop the upload
        raise HTTPException(
            status_code=402,
            detail={"reason": "no_credits", "buy_url_base": GUMROAD_CHECKOUT_BASE},
        )
    credits_remaining = reserve_entry.balance_after

    await db.commit()
    await db.refresh(job)

    # Public jobs run on the ISOLATED arq:ai-public queue (capped worker) so a
    # spike can't starve member analyses on arq:ai.
    await _enqueue_analysis(job.id, queue_name=PUBLIC_QUEUE_NAME)

    return PublicAnalysisJobResponse(
        job_id=job.id,
        status=_status_str(job),
        stroke_type=job.stroke_type,
        guest_token=guest_token,
        credits_remaining=credits_remaining,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
    )


# ── GET /ai/public/analyze/{job_id} ──────────────────────────────


@router.get(
    "/analyze/{job_id}",
    response_model=PublicAnalysisJobDetailResponse,
    summary="Poll a guest Stroke Lab job's status + result (guest_token)",
)
async def get_public_analysis_job(
    job_id: uuid.UUID,
    x_guest_token: Annotated[Optional[str], Header(alias="X-Guest-Token")] = None,
    guest_token: Annotated[Optional[str], Query()] = None,
    db: AsyncSession = Depends(get_async_db),
) -> PublicAnalysisJobDetailResponse:
    token = x_guest_token or guest_token
    job = await db.get(AnalysisJob, job_id)

    # 404 (never 403) on any failure — missing job, a member job, or a token
    # mismatch all look identical, so we never leak which job_ids exist.
    if (
        job is None
        or job.source != AnalysisJobSource.PUBLIC
        or not token
        or not job.guest_token
        or not secrets.compare_digest(token, job.guest_token)
    ):
        raise HTTPException(status_code=404, detail="Job not found")

    result_row: Optional[AnalysisResult] = None
    original_url: Optional[str] = None
    annotated_url: Optional[str] = None
    if job.status == AnalysisJobStatus.COMPLETED:
        rs = await db.execute(
            select(AnalysisResult).where(AnalysisResult.job_id == job_id)
        )
        result_row = rs.scalar_one_or_none()
        try:
            if job.video_storage_path:
                original_url = await signed_url_for_upload(job.video_storage_path)
        except Exception as exc:
            logger.warning(
                "Could not sign upload URL for public job %s: %s", job.id, exc
            )
        try:
            if job.annotated_video_storage_path:
                annotated_url = await signed_url_for_annotated(
                    job.annotated_video_storage_path
                )
        except Exception as exc:
            logger.warning(
                "Could not sign annotated URL for public job %s: %s", job.id, exc
            )

    payload = build_result_payload(result_row) if result_row is not None else None
    return PublicAnalysisJobDetailResponse(
        job_id=job.id,
        status=_status_str(job),
        stroke_type=job.stroke_type,
        error_message=job.error_message,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        result=payload,
        original_video_url=original_url,
        annotated_video_url=annotated_url,
    )
