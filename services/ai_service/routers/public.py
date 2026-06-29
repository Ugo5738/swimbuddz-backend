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

import httpx
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.logging import get_logger
from libs.common.service_client.media import (
    create_media_direct_upload,
    verify_media_object,
)
from libs.db.session import get_async_db
from services.ai_service.analysis.storage import (
    delete_job_assets,
    is_media_storage_path,
    media_object_key,
    media_storage_path,
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
from services.ai_service.routers._common import (
    build_result_payload,
    parse_coach_context,
    sign_coach_evidence,
    sign_coach_share,
)
from services.ai_service.routers.analyze import (
    MAX_UPLOAD_BYTES,
    SUPPORTED_STROKES,
    _enqueue_analysis,
    _queue_depth,
    _start_inspect_job,
)
from services.ai_service.schemas.analysis import (
    GumroadRedeemRequest,
    GumroadRedeemResponse,
    InspectRequest,
    PublicAnalysisJobDetailResponse,
    PublicAnalysisJobResponse,
    PublicCreditsResponse,
    PublicDirectUploadRequest,
    PublicDirectUploadResponse,
)
from services.ai_service.services.credit_ops import (
    PERMALINK_CREDITS,
    NoCreditsError,
    acquire_for_submit,
    find_reservation,
    find_sale_grant,
    get_balance,
    grant_paid,
    refund_reservation,
    revoke_sale,
)
from services.ai_service.services.drilldown import (
    drilldown_unlocked,
    ensure_drilldown_unlocked,
    existing_inspect_finding,
    timeline_view_unlocked,
    validate_inspect,
)
from services.ai_service.services.gumroad import (
    GUMROAD_PING_TOKEN,
    GUMROAD_SELLER_ID,
    verify_license,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/public", tags=["stroke-lab-public"])

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_DIRECT_UPLOAD_TTL_SECONDS = 900


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


def _ready_hint(depth: int | None) -> str:
    if depth is None or depth <= 1:
        return "We'll email you a link as soon as it's ready."
    return (
        f"You're in the analysis queue behind about {depth - 1} clip"
        f"{'' if depth - 1 == 1 else 's'}. We'll email you a link as soon as it's ready."
    )


# ── POST /ai/public/analyze ──────────────────────────────────────


@router.post(
    "/analyze/uploads",
    response_model=PublicDirectUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a guest job and issue a media-service upload URL",
)
async def create_public_direct_upload(
    req: PublicDirectUploadRequest,
    db: AsyncSession = Depends(get_async_db),
) -> PublicDirectUploadResponse:
    """Issue a media-service-owned presigned PUT target for the analyzer.

    This is the preferred browser path for real videos: the API never buffers the
    multipart body, and analysis is not queued until /complete verifies the object.
    The legacy multipart endpoint below remains as a fallback for local/dev.
    """
    if req.stroke_type not in SUPPORTED_STROKES:
        raise HTTPException(
            status_code=400,
            detail=f"Only freestyle is supported. Got: {req.stroke_type}",
        )
    email = _normalize_email(req.guest_email)
    if req.size_bytes <= 0:
        raise HTTPException(status_code=400, detail="Empty video upload")
    if req.size_bytes > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Video exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit",
        )

    # Preflight the credit gate so a user without credits doesn't upload a large
    # file only to be rejected. The complete step still reserves atomically, so a
    # race between two tabs is handled correctly there.
    bal = await get_balance(db, raw_email=email)
    if not bal["can_submit_free"] and bal["remaining_credits"] <= 0:
        raise HTTPException(
            status_code=402,
            detail={"reason": "no_credits", "buy_url_base": GUMROAD_CHECKOUT_BASE},
        )

    guest_token = secrets.token_urlsafe(32)
    job = AnalysisJob(
        member_auth_id=None,
        guest_email=email,
        guest_token=guest_token,
        source=AnalysisJobSource.PUBLIC,
        stroke_type=req.stroke_type,
        video_storage_path="",
        status=AnalysisJobStatus.PENDING,
        **parse_coach_context(req.discipline, req.level, req.focus_area, req.goal_text),
    )
    db.add(job)
    await db.flush()

    try:
        upload = await create_media_direct_upload(
            purpose="strokelab_original",
            filename=req.filename,
            content_type=req.content_type or "video/mp4",
            size_bytes=req.size_bytes,
            linked_id=f"guest/{guest_token}/{job.id}",
            expires_in=_DIRECT_UPLOAD_TTL_SECONDS,
            calling_service="ai_service",
        )
    except httpx.HTTPStatusError as exc:
        await db.rollback()
        logger.exception("Media direct-upload init failed: %s", exc)
        code = exc.response.status_code if exc.response is not None else 502
        raise HTTPException(
            status_code=501 if code == 501 else 502,
            detail="Direct upload is not available in this environment."
            if code == 501
            else "Could not initialize storage upload.",
        ) from exc
    except Exception as exc:
        await db.rollback()
        logger.exception("Could not create public direct upload: %s", exc)
        raise HTTPException(
            status_code=502,
            detail="Could not initialize storage upload.",
        ) from exc

    job.video_storage_path = media_storage_path(str(upload["object_key"]))
    await db.commit()
    return PublicDirectUploadResponse(
        job_id=job.id,
        guest_token=guest_token,
        upload_url=str(upload["upload_url"]),
        headers=dict(upload.get("headers") or {}),
        expires_in=int(upload.get("expires_in") or _DIRECT_UPLOAD_TTL_SECONDS),
    )


@router.post(
    "/analyze/{job_id}/complete-upload",
    response_model=PublicAnalysisJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Verify a direct upload, reserve credit, and queue analysis",
)
async def complete_public_direct_upload(
    job_id: uuid.UUID,
    x_guest_token: Annotated[Optional[str], Header(alias="X-Guest-Token")] = None,
    guest_token: Annotated[Optional[str], Query()] = None,
    db: AsyncSession = Depends(get_async_db),
) -> PublicAnalysisJobResponse:
    token = x_guest_token or guest_token
    job = await db.get(AnalysisJob, job_id)
    if (
        job is None
        or job.source != AnalysisJobSource.PUBLIC
        or not token
        or not job.guest_token
        or not secrets.compare_digest(token, job.guest_token)
    ):
        raise HTTPException(status_code=404, detail="Job not found")
    existing_reserve = await find_reservation(db, job_id=job.id)
    if job.status in (AnalysisJobStatus.PROCESSING, AnalysisJobStatus.COMPLETED) or (
        job.status == AnalysisJobStatus.PENDING and existing_reserve is not None
    ):
        depth = await _queue_depth(PUBLIC_QUEUE_NAME)
        return PublicAnalysisJobResponse(
            job_id=job.id,
            status=_status_str(job),
            stroke_type=job.stroke_type,
            guest_token=job.guest_token,
            credits_remaining=0,
            queue_depth=depth,
            estimated_ready_hint=_ready_hint(depth),
            created_at=job.created_at,
            started_at=job.started_at,
            completed_at=job.completed_at,
        )
    if job.status != AnalysisJobStatus.PENDING:
        raise HTTPException(status_code=409, detail="Upload is not active")
    if not job.video_storage_path:
        raise HTTPException(status_code=409, detail="Upload was not initialized")

    if not is_media_storage_path(job.video_storage_path):
        raise HTTPException(status_code=409, detail="Upload cannot be verified")

    try:
        meta = await verify_media_object(
            object_key=media_object_key(job.video_storage_path),
            calling_service="ai_service",
        )
    except Exception as exc:
        raise HTTPException(
            status_code=409, detail="Upload is not complete yet"
        ) from exc
    size = int(meta.get("size_bytes") or 0)
    if size <= 0:
        raise HTTPException(status_code=400, detail="Empty video upload")
    if size > MAX_UPLOAD_BYTES:
        await delete_job_assets(job.video_storage_path, None)
        await db.delete(job)
        await db.commit()
        raise HTTPException(
            status_code=413,
            detail=f"Video exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit",
        )

    try:
        reserve_entry = await acquire_for_submit(
            db, raw_email=job.guest_email or "", job_id=job.id
        )
    except NoCreditsError:
        await delete_job_assets(job.video_storage_path, None)
        await db.delete(job)
        await db.commit()
        raise HTTPException(
            status_code=402,
            detail={"reason": "no_credits", "buy_url_base": GUMROAD_CHECKOUT_BASE},
        )

    job.status = AnalysisJobStatus.PENDING
    credits_remaining = reserve_entry.balance_after
    await db.commit()
    await db.refresh(job)

    try:
        await _enqueue_analysis(
            job.id, queue_name=PUBLIC_QUEUE_NAME, raise_on_error=True
        )
    except Exception as exc:
        logger.exception("Public enqueue failed for job %s: %s", job.id, exc)
        await refund_reservation(db, raw_email=job.guest_email or "", job_id=job.id)
        job.status = AnalysisJobStatus.FAILED
        job.error_message = "Could not queue analysis"
        await db.commit()
        raise HTTPException(
            status_code=502,
            detail="Could not queue your analysis; your credit was refunded. Please try again.",
        ) from exc

    depth = await _queue_depth(PUBLIC_QUEUE_NAME)
    return PublicAnalysisJobResponse(
        job_id=job.id,
        status=_status_str(job),
        stroke_type=job.stroke_type,
        guest_token=job.guest_token or "",
        credits_remaining=credits_remaining,
        queue_depth=depth,
        estimated_ready_hint=_ready_hint(depth),
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
    )


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
    discipline: Annotated[str, Form()] = "general",
    level: Annotated[str | None, Form()] = None,
    focus_area: Annotated[str | None, Form()] = None,
    goal_text: Annotated[str | None, Form()] = None,
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
        **parse_coach_context(discipline, level, focus_area, goal_text),
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
    # spike can't starve member analyses on arq:ai. The reserve is already
    # committed, so if enqueue fails we MUST refund it and fail the job (§4.1) —
    # otherwise the credit is lost on a job that would sit forever in PENDING.
    try:
        await _enqueue_analysis(
            job.id, queue_name=PUBLIC_QUEUE_NAME, raise_on_error=True
        )
    except Exception as exc:
        logger.exception("Public enqueue failed for job %s: %s", job.id, exc)
        await refund_reservation(db, raw_email=email, job_id=job.id)
        job.status = AnalysisJobStatus.FAILED
        job.error_message = "Could not queue analysis"
        await db.commit()
        raise HTTPException(
            status_code=502,
            detail="Could not queue your analysis; your credit was refunded. Please try again.",
        ) from exc

    depth = await _queue_depth(PUBLIC_QUEUE_NAME)
    return PublicAnalysisJobResponse(
        job_id=job.id,
        status=_status_str(job),
        stroke_type=job.stroke_type,
        guest_token=guest_token,
        credits_remaining=credits_remaining,
        queue_depth=depth,
        estimated_ready_hint=_ready_hint(depth),
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
    # Expose the result while PROCESSING too — the worker writes a partial
    # coach_result after each stage (progressive rendering), so the page can
    # render finished sections instead of a blank "analyzing" wait.
    if job.status in (AnalysisJobStatus.COMPLETED, AnalysisJobStatus.PROCESSING):
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
    if payload is not None and result_row is not None:
        payload.coach_evidence_urls = await sign_coach_evidence(result_row)
        payload.coach_share_urls = await sign_coach_share(result_row)
    depth = (
        await _queue_depth(PUBLIC_QUEUE_NAME)
        if job.status in (AnalysisJobStatus.PENDING, AnalysisJobStatus.PROCESSING)
        else None
    )
    return PublicAnalysisJobDetailResponse(
        job_id=job.id,
        status=_status_str(job),
        stroke_type=job.stroke_type,
        discipline=job.discipline,
        drilldown_unlocked=drilldown_unlocked(),
        timeline_unlocked=timeline_view_unlocked(),
        error_message=job.error_message,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        result=payload,
        original_video_url=original_url,
        annotated_video_url=annotated_url,
        queue_depth=depth,
    )


# ── POST /ai/public/analyze/{job_id}/inspect (drilldown, §12.5) ──────────────


@router.post(
    "/analyze/{job_id}/inspect",
    summary="Coach one stored instance on demand, guest (gated; 409 until unlocked)",
)
async def inspect_public_analysis(
    job_id: uuid.UUID,
    req: InspectRequest,
    x_guest_token: Annotated[Optional[str], Header(alias="X-Guest-Token")] = None,
    guest_token: Annotated[Optional[str], Query()] = None,
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    token = x_guest_token or guest_token
    job = await db.get(AnalysisJob, job_id)
    if (
        job is None
        or job.source != AnalysisJobSource.PUBLIC
        or not token
        or not job.guest_token
        or not secrets.compare_digest(token, job.guest_token)
    ):
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
            queue_name=PUBLIC_QUEUE_NAME,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Could not queue inspect") from exc


# ── POST /ai/public/analyze/{job_id}/retry (re-run a failed job, free) ───────


@router.post(
    "/analyze/{job_id}/retry",
    summary="Re-run a FAILED guest analysis on its stored clip — free (guest_token)",
)
async def retry_public_analysis(
    job_id: uuid.UUID,
    x_guest_token: Annotated[Optional[str], Header(alias="X-Guest-Token")] = None,
    guest_token: Annotated[Optional[str], Query()] = None,
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    token = x_guest_token or guest_token
    job = await db.get(AnalysisJob, job_id)
    if (
        job is None
        or job.source != AnalysisJobSource.PUBLIC
        or not token
        or not job.guest_token
        or not secrets.compare_digest(token, job.guest_token)
    ):
        raise HTTPException(status_code=404, detail="Job not found")
    # Only a FAILED job is retryable; the credit was already refunded on failure, so
    # the re-run is FREE (a transient hiccup shouldn't cost the user a clip).
    if job.status != AnalysisJobStatus.FAILED:
        raise HTTPException(
            status_code=409,
            detail={
                "reason": "not_retryable",
                "message": "Only a failed analysis can be retried.",
            },
        )
    if not job.video_storage_path:
        raise HTTPException(
            status_code=409,
            detail={
                "reason": "clip_gone",
                "message": "The original clip is no longer available — please upload again.",
            },
        )
    job.status = AnalysisJobStatus.PENDING
    job.error_message = None
    job.started_at = None
    job.completed_at = None
    await db.commit()
    try:
        await _enqueue_analysis(
            job.id, queue_name=PUBLIC_QUEUE_NAME, raise_on_error=True
        )
    except Exception as exc:
        job.status = AnalysisJobStatus.FAILED
        job.error_message = "temporarily_unavailable"
        await db.commit()
        raise HTTPException(status_code=502, detail="Could not queue retry") from exc
    return {"status": "queued"}


# ── GET /ai/public/credits ───────────────────────────────────────


@router.get(
    "/credits",
    response_model=PublicCreditsResponse,
    summary="Coarse analyzer credit balance for an email",
)
async def get_public_credits(
    email: str,
    db: AsyncSession = Depends(get_async_db),
) -> PublicCreditsResponse:
    """Coarse, non-enumerable balance for the paywall. ``free_used`` is not
    exposed (it is the 'has this email been used' leak — design §4.3)."""
    normalized = _normalize_email(email)
    bal = await get_balance(db, raw_email=normalized)
    return PublicCreditsResponse(
        email=normalized,
        can_submit_free=bal["can_submit_free"],
        remaining_credits=bal["remaining_credits"],
    )


# ── POST /ai/public/credits/redeem ───────────────────────────────


@router.post(
    "/credits/redeem",
    response_model=GumroadRedeemResponse,
    summary="Redeem a Gumroad license key for analyzer credits",
)
async def redeem_license(
    body: GumroadRedeemRequest,
    db: AsyncSession = Depends(get_async_db),
) -> GumroadRedeemResponse:
    """Different-email fallback: a buyer whose Gumroad email differs from their
    analyzer email pastes their license key. Verified against Gumroad; idempotent
    on the sale so the webhook + redeem can't double-credit."""
    email = _normalize_email(body.email)
    permalink = (body.product_permalink or "").strip()
    license_key = (body.license_key or "").strip()
    if permalink not in PERMALINK_CREDITS:
        raise HTTPException(status_code=400, detail={"reason": "unknown_product"})

    purchase = await verify_license(permalink, license_key)
    sale_id = (purchase or {}).get("sale_id")
    if not purchase or not sale_id:
        raise HTTPException(status_code=422, detail={"reason": "license_invalid"})

    if (existing := await find_sale_grant(db, sale_id=sale_id)) is not None:
        # Reveal only the email the sale already credited, to the key holder.
        raise HTTPException(
            status_code=409,
            detail={"reason": "already_redeemed", "email": existing.email},
        )

    entry = await grant_paid(
        db,
        raw_email=email,
        permalink=permalink,
        sale_id=sale_id,
        license_key=license_key,
    )
    if entry is None:
        raise HTTPException(status_code=400, detail={"reason": "unknown_product"})
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail={"reason": "already_redeemed"})
    return GumroadRedeemResponse(
        granted=entry.amount, remaining_credits=entry.balance_after
    )


# ── POST /ai/public/gumroad/webhook ──────────────────────────────


@router.post(
    "/gumroad/webhook",
    summary="Gumroad Ping — grant credits on sale, revoke on refund/dispute",
)
async def gumroad_webhook(
    request: Request,
    token: Annotated[Optional[str], Query()] = None,
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    """Gumroad has no signature, so we layer three checks (design §7.2): an
    unguessable shared-secret path token, a seller_id match, and a MANDATORY
    license re-verify before granting. Always returns 200 (except a bad path
    token → 403) so Gumroad does not retry-storm."""
    # 1. Shared-secret path token — reject before parsing the body.
    if (
        not GUMROAD_PING_TOKEN
        or not token
        or not secrets.compare_digest(token, GUMROAD_PING_TOKEN)
    ):
        raise HTTPException(status_code=403, detail="forbidden")

    form = await request.form()
    seller_id = str(form.get("seller_id") or "").strip()
    sale_id = str(form.get("sale_id") or "").strip()
    buyer_email = str(form.get("email") or "").strip()
    permalink = str(
        form.get("product_permalink") or form.get("permalink") or ""
    ).strip()
    license_key = str(form.get("license_key") or "").strip()
    refunded = str(form.get("refunded") or "").lower() in ("true", "1")
    disputed = str(form.get("disputed") or "").lower() in ("true", "1")

    # 2. seller_id match (cheap sanity filter; not a secret). When configured, a
    # missing/wrong seller_id is treated as a mismatch (200-and-drop) so it
    # can't be bypassed by simply omitting the field.
    if GUMROAD_SELLER_ID and seller_id != GUMROAD_SELLER_ID:
        logger.warning("Gumroad webhook seller_id mismatch (%r)", seller_id)
        return {"received": True}
    if not sale_id or not buyer_email or not permalink:
        return {"received": True}

    try:
        if refunded or disputed:
            # Revoke against the account the sale actually credited (resolved by
            # sale_id), NOT the Ping's buyer email — a redeemed sale may have
            # credited a different analyzer email.
            await revoke_sale(db, sale_id=sale_id)
            await db.commit()
        else:
            # 3. MANDATORY license re-verify — a sale Ping with no verifiable key
            # is NEVER granted (seller_id alone is public, never sufficient).
            if not license_key:
                return {"received": True}
            purchase = await verify_license(permalink, license_key)
            if not purchase or purchase.get("sale_id") != sale_id:
                logger.info(
                    "Gumroad webhook verify failed / sale mismatch: %s", sale_id
                )
                return {"received": True}
            await grant_paid(
                db,
                raw_email=buyer_email,
                permalink=permalink,
                sale_id=sale_id,
                license_key=license_key,
            )
            await db.commit()
    except Exception as exc:  # noqa: BLE001 — the webhook must always ack
        await db.rollback()
        logger.exception("Gumroad webhook error for sale %s: %s", sale_id, exc)
    return {"received": True}
