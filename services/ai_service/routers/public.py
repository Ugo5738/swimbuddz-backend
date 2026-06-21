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
    Request,
    UploadFile,
    status,
)
from libs.common.logging import get_logger
from libs.db.session import get_async_db
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
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
)
from services.ai_service.schemas.analysis import (
    GumroadRedeemRequest,
    GumroadRedeemResponse,
    InspectRequest,
    PublicAnalysisJobDetailResponse,
    PublicAnalysisJobResponse,
    PublicCreditsResponse,
)
from services.ai_service.services.drilldown import (
    ensure_drilldown_unlocked,
    run_inspect,
)
from services.ai_service.services.credit_ops import (
    PERMALINK_CREDITS,
    NoCreditsError,
    acquire_for_submit,
    find_sale_grant,
    get_balance,
    grant_paid,
    refund_reservation,
    revoke_sale,
)
from services.ai_service.services.gumroad import (
    GUMROAD_PING_TOKEN,
    GUMROAD_SELLER_ID,
    verify_license,
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
    if payload is not None and result_row is not None:
        payload.coach_evidence_urls = await sign_coach_evidence(result_row)
        payload.coach_share_urls = await sign_coach_share(result_row)
    return PublicAnalysisJobDetailResponse(
        job_id=job.id,
        status=_status_str(job),
        stroke_type=job.stroke_type,
        discipline=job.discipline,
        error_message=job.error_message,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        result=payload,
        original_video_url=original_url,
        annotated_video_url=annotated_url,
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
    if result_row is None:
        raise HTTPException(status_code=404, detail="No analysis result to inspect")
    return run_inspect(result_row, req.aspect, req.instance_id)


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
