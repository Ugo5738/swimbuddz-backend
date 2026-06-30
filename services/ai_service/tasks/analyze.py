"""ARQ task: run Stroke Lab analysis on one queued job.

The VLM coach is the PRIMARY engine (the legacy pose/metrics engine is retired).

Lifecycle:
  1. Worker picks up the job, loads the AnalysisJob row.
  2. Status flips to PROCESSING + started_at set.
  3. Upload pulled from Supabase to a tempfile.
  4. Coach pipeline runs (gate → classify → per-instance + holistic).
  5. Evidence/share frames uploaded; AnalysisResult row written.
  6. Job status flips to COMPLETED + completed_at set.
  7. Tempfiles cleaned up.

Failure semantics: a coach exception → FAILED + (public) credit refunded. A
gate REFUSAL (a clip we can't read) → FAILED("could_not_track") + refunded —
the swimmer is never charged for a clip we couldn't coach, and the result page
shows the "film a side-on clip" guidance. The pose-derived observability
columns (pose_detection_rate, frames_*) are honestly NULL on coach runs.
"""

from __future__ import annotations

import asyncio
import tempfile
import uuid
from datetime import timedelta
from pathlib import Path

from arq import create_pool
from sqlalchemy import delete, select

from libs.common.arq_config import get_redis_settings
from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from libs.db.config import AsyncSessionLocal
from services.ai_service.analysis.storage import (
    download_storage_path,
    upload_evidence_frames,
)
from services.ai_service.constants import (
    MEMBER_QUEUE_NAME,
    PUBLIC_MAX_DURATION_SECONDS,
    PUBLIC_QUEUE_NAME,
)
from services.ai_service.models import (
    AnalysisJob,
    AnalysisJobSource,
    AnalysisJobStatus,
    AnalysisResult,
    SwimFrameLabel,
)
from services.ai_service.pipeline.types import CoachContext  # import-light (no cv2)
from services.ai_service.providers.base import (
    start_vlm_usage_capture,
    stop_vlm_usage_capture,
)
from services.ai_service.services.credit_ops import (
    consume_reservation,
    refund_reservation,
)
from services.ai_service.services.notify import send_failed_email, send_ready_email
from services.ai_service.services.provider_usage import (
    gemini_quota_estimate,
    record_usage_events,
)

logger = get_logger(__name__)

_MAIN_COACH_COMPONENT = "chunk_coach"
_MAIN_COACH_AUTO_RETRY_DELAYS_SECONDS = (60, 180, 600)
_RETRYABLE_COACH_ERROR_MARKERS = (
    "internalservererror",
    "serviceunavailable",
    "ratelimit",
    "rate limit",
    "timeout",
    "timed out",
    "resourceexhausted",
    "resource exhausted",
    "temporarily unavailable",
    "overloaded",
    "high demand",
    "500",
    "503",
    "429",
)


async def analyze_swim_video(job_id: str) -> dict:
    """Public ARQ entrypoint. Returns a small status dict for the queue UI."""
    job_uuid = uuid.UUID(job_id)
    logger.info("Stroke Lab: starting analysis for job %s", job_id)

    # 1. Load + mark PROCESSING
    async with AsyncSessionLocal() as session:
        job = await session.get(AnalysisJob, job_uuid)
        if job is None:
            logger.warning("Stroke Lab job %s vanished before pickup", job_id)
            return {"status": "missing"}
        if job.status == AnalysisJobStatus.COMPLETED:
            logger.info("Stroke Lab job %s already completed; skipping", job_id)
            return {"status": "already_done"}
        job.status = AnalysisJobStatus.PROCESSING
        job.started_at = utc_now()
        job.error_message = None
        await session.commit()
        await session.refresh(job)
        member_auth_id = job.member_auth_id
        stroke_type = job.stroke_type
        video_storage_path = job.video_storage_path
        source = job.source
        guest_token = job.guest_token
        existing = await session.execute(
            select(AnalysisResult).where(AnalysisResult.job_id == job_uuid)
        )
        existing_result = existing.scalar_one_or_none()
        existing_payload = (
            existing_result.coach_result
            if existing_result is not None and existing_result.coach_result
            else {}
        )
        replay_cache = existing_payload.get("cache") or None
        retry_attempt = int((existing_payload.get("retry") or {}).get("attempt") or 0)
        # Goal-aware coaching context (§12) — captured while the session is open.
        coach_context = CoachContext(
            discipline=job.discipline,
            level=job.level,
            focus_area=job.focus_area,
            goal_text=job.goal_text,
        )

    capture_token = start_vlm_usage_capture()

    def _collect_usage_events() -> list[dict]:
        nonlocal capture_token
        if capture_token is None:
            return []
        events = stop_vlm_usage_capture(capture_token)
        capture_token = None
        return events

    # 2. The VLM coach is the analysis now — PRIMARY + REQUIRED. A coach
    # exception propagates to the failure path (FAILED + refund); a gate refusal
    # is handled explicitly below.
    try:
        coach_payload: dict | None = None
        frame_labels: list[dict] | None = None
        with tempfile.TemporaryDirectory(prefix="strokelab_") as workdir:
            workdir_path = Path(workdir)

            # Pull the upload into our workdir through media_service.
            uploaded_local = await _download_upload(video_storage_path, workdir_path)

            # Public DoS guard: reject over-long clips BEFORE the expensive
            # pipeline. The client also fast-fails on duration; this catches
            # uploads that bypassed it. _mark_failed refunds the credit (a
            # rejected clip never costs the guest). Members are unaffected.
            if source == AnalysisJobSource.PUBLIC:
                duration = await asyncio.to_thread(
                    _probe_duration_seconds, uploaded_local
                )
                if duration is not None and duration > PUBLIC_MAX_DURATION_SECONDS:
                    logger.info(
                        "Public job %s rejected: %.1fs exceeds %ss cap",
                        job_id,
                        duration,
                        PUBLIC_MAX_DURATION_SECONDS,
                    )
                    await _mark_failed(
                        job_uuid, "too_long", usage_events=_collect_usage_events()
                    )
                    return {"status": "failed", "error": "too_long"}

            # Persist each stage as it finishes so the result page renders
            # section-by-section (progressive rendering). Best-effort.
            async def _persist_progress(partial) -> None:
                await _write_partial(job_uuid, partial)

            # Run the coach (gate → classify → per-instance + holistic). A raise
            # here propagates to the failure path below.
            coach_payload = await _run_coach_pipeline(
                uploaded_local,
                coach_context,
                on_progress=_persist_progress,
                replay_cache=replay_cache,
            )
            if coach_payload is None:
                # Coach disabled (STROKELAB_ENABLE_COACH off) — there is no
                # fallback engine now, so the job can't produce a result.
                logger.error("Coach disabled but job %s reached the worker", job_id)
                await _mark_failed(
                    job_uuid,
                    "coach_unavailable",
                    usage_events=_collect_usage_events(),
                )
                return {"status": "failed", "error": "coach_unavailable"}

            # Gate refusal: an above-water/side-on clip we can't read. Refund and
            # surface the "film a side-on clip" guidance — we won't guess.
            if _coach_refused(coach_payload):
                logger.info("Stroke Lab job %s refused by the gate", job_id)
                await _mark_failed(
                    job_uuid,
                    "could_not_track",
                    usage_events=_collect_usage_events(),
                )
                return {"status": "refused"}

            retryable_error = _main_coach_retryable_error(coach_payload)
            if retryable_error:
                next_attempt = retry_attempt + 1
                if next_attempt <= len(_MAIN_COACH_AUTO_RETRY_DELAYS_SECONDS):
                    delay = _MAIN_COACH_AUTO_RETRY_DELAYS_SECONDS[next_attempt - 1]
                    await _mark_coach_retrying(
                        job_uuid,
                        coach_payload,
                        attempt=next_attempt,
                        delay_seconds=delay,
                        usage_events=_collect_usage_events(),
                    )
                    logger.warning(
                        "Stroke Lab job %s will retry AI coach in %ss "
                        "(attempt %s/%s): %s",
                        job_id,
                        delay,
                        next_attempt,
                        len(_MAIN_COACH_AUTO_RETRY_DELAYS_SECONDS),
                        retryable_error[:200],
                    )
                    return {
                        "status": "retrying",
                        "attempt": next_attempt,
                        "delay_seconds": delay,
                    }
                logger.warning(
                    "Stroke Lab job %s exhausted AI coach retries: %s",
                    job_id,
                    retryable_error[:200],
                )
                await _mark_failed(
                    job_uuid,
                    "temporarily_unavailable",
                    usage_events=_collect_usage_events(),
                )
                return {"status": "failed", "error": "temporarily_unavailable"}

            # Non-refused but empty: a coach component errored and was swallowed,
            # leaving no real findings. Don't charge for a blank read — refund and
            # give the same "film a clearer clip" guidance.
            if not _coach_has_content(coach_payload):
                logger.warning("Stroke Lab job %s produced no coaching content", job_id)
                await _mark_failed(
                    job_uuid,
                    "could_not_track",
                    usage_events=_collect_usage_events(),
                )
                return {"status": "failed", "error": "no_coaching"}

            # Pop the transient image BYTES (never store them in the JSON column)
            # and upload best-effort → coaching survives an upload failure. The
            # *_keys maps (label → storage key) are JSON-safe. Per-frame labels go
            # to the normalized table (cache["labels"] keeps the JSON replay copy).
            evidence = coach_payload.pop("evidence", None)
            share_cards = coach_payload.pop("share_cards", None)
            frame_labels = coach_payload.pop("frame_labels", None)
            prefix = (
                f"guest/{guest_token}"
                if source == AnalysisJobSource.PUBLIC
                else str(member_auth_id)
            )
            if evidence:
                try:
                    coach_payload["evidence_keys"] = await upload_evidence_frames(
                        prefix, job_uuid, evidence
                    )
                except Exception:
                    logger.exception(
                        "Evidence upload failed for job %s — coaching kept", job_id
                    )
            if share_cards:
                try:
                    coach_payload["share_keys"] = await upload_evidence_frames(
                        prefix, job_uuid, share_cards, subdir="share"
                    )
                except Exception:
                    logger.exception(
                        "Share-card upload failed for job %s — coaching kept",
                        job_id,
                    )

        # 3. Persist result row + flip status. detected_stroke echoes the
        # requested stroke; the pose-derived observability columns are NULL.
        await _write_completed(
            job_uuid,
            stroke_type,
            coach_payload,
            frame_labels,
            usage_events=_collect_usage_events(),
        )
        logger.info("Stroke Lab job %s completed (coach-primary)", job_id)
        return {"status": "completed"}
    except Exception as exc:  # pragma: no cover — failure path
        logger.exception("Stroke Lab job %s failed", job_id)
        reason = _failure_reason(exc)
        await _mark_failed(job_uuid, reason, usage_events=_collect_usage_events())
        return {"status": "failed", "error": reason}


# ── Internal helpers ──────────────────────────────────────────────


def _coach_refused(coach_payload: dict) -> bool:
    """True when the gate refused the clip — the encoded PipelineResult flags it.
    A refusal is a valid 'we can't read this' outcome (handled as a refund), not a
    crash."""
    return bool((coach_payload.get("result") or {}).get("refused"))


def _coach_has_content(coach_payload: dict) -> bool:
    """True when the coach produced at least one real (available) finding. A
    non-refused run with NO content means a coach component errored and was
    swallowed by the pipeline's _safe_run — we won't charge for a blank read.
    'unavailable' placeholders (the underwater can't-see cards) don't count."""
    results = (coach_payload.get("result") or {}).get("results") or []
    for cr in results:
        if cr.get("component") in {"gate", "collate"}:
            continue
        for f in cr.get("findings") or []:
            if f.get("available", True) and f.get("severity") in (
                "fix",
                "strength",
                "info",
            ):
                return True
    return False


def _main_coach_retryable_error(coach_payload: dict) -> str | None:
    """Return the transient main-coach error, if this run should auto-retry.

    The count/collate stages can succeed without any coaching content. For the
    product, ``chunk_coach`` is the essential read: if it fails on a transient
    Gemini/LLM error, keep the job alive and retry instead of completing an empty
    result.
    """
    results = (coach_payload.get("result") or {}).get("results") or []
    for cr in results:
        if cr.get("component") != _MAIN_COACH_COMPONENT:
            continue
        err = str(cr.get("error") or "")
        if not err:
            return None
        low = err.lower()
        if any(marker in low for marker in _RETRYABLE_COACH_ERROR_MARKERS):
            return err
    return None


async def _download_upload(storage_path: str, workdir: Path) -> Path:
    """Pull the user's upload into the worker's temp workdir."""
    return await download_storage_path(storage_path, workdir)


def _probe_duration_seconds(path: Path) -> float | None:
    """Cheap duration probe via cv2 metadata (no full decode). Returns None if
    it can't be determined — then the pipeline proceeds and job_timeout is the
    backstop. Worker-only (cv2 ships in the strokelab image, not the API/dev
    env)."""
    import cv2

    cap = cv2.VideoCapture(str(path))
    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
        frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0
    finally:
        cap.release()
    if fps <= 0 or frames <= 0:
        return None
    return frames / fps


def _coach_enabled() -> bool:
    from libs.common.config import get_settings

    return bool(get_settings().STROKELAB_ENABLE_COACH)


# Phrases that mean "the camera couldn't see this" — a caveat, never a strength.
_CANT_SEE_PHRASES = (
    "not visible",
    "not clearly visible",
    "isn't visible",
    "can't see",
    "cannot see",
    "not clear",
    "below the surface",
    "hard to make out",
)


def _apply_borderline_honesty(result) -> None:
    """A BORDERLINE clip is a marginal camera angle the coach can't actually verify
    specifics on — so quiet the read rather than let it assert things it can't stand
    behind (a swimmer saw a confident "high elbow recovery" cited on a frame where he
    hadn't started stroking). On borderline: drop the per-frame "watch this moment"
    citations (we can't trust the frame), demote "can't see X" out of strengths
    (absence isn't a win), and cap confidence. No-op on CLEAN clips."""
    from services.ai_service.pipeline.types import (
        SEVERITY_INFO,
        SEVERITY_STRENGTH,
        GateTier,
    )

    if result.gate_tier != GateTier.BORDERLINE:
        return
    for cr in result.results:
        for f in cr.findings:
            f.evidence_frames = []  # marginal angle → no frame we can stand behind
            f.confidence = min(f.confidence, 0.4)
            if f.severity == SEVERITY_STRENGTH and any(
                p in f.observation.lower() for p in _CANT_SEE_PHRASES
            ):
                f.severity = SEVERITY_INFO  # "can't see X" is a caveat, not a win


async def _run_coach_pipeline(
    video_path: Path,
    coach_context: CoachContext | None = None,
    on_progress=None,
    replay_cache: dict | None = None,
) -> dict | None:
    """Run the VLM-coach pipeline; return a JSON-serialisable stored run.

    Shape: {"engine_version", "result": <PipelineResult>, "cache": <vlm cache>,
    "evidence": {"<component>:<index>": <jpeg bytes>}}. The cache holds the PAID
    VLM outputs (re-derive findings free). "evidence" is TRANSIENT frame bytes the
    caller uploads and replaces with storage keys — never store it in the column.
    ``coach_context`` carries the swimmer's goal (§12); it steers grading + framing,
    never perception. Models come from config (overridable per-env). None when off.
    """
    if not _coach_enabled():
        return None
    import cv2

    from libs.common.config import get_settings
    from services.ai_service.coach.frames import extract_key_frames
    from services.ai_service.pipeline.defaults import build_default_registry
    from services.ai_service.pipeline.runner import run_pipeline
    from services.ai_service.pipeline.store import _enc
    from services.ai_service.pipeline.types import (
        InputProfile,
        Phase,
        PipelineConfig,
        RunContext,
    )

    try:
        from services.ai_service.analysis.version import STROKELAB_ENGINE_VERSION as ver
    except Exception:
        ver = "unknown"

    s = get_settings()
    config = PipelineConfig(
        gate_model=s.STROKELAB_COACH_GATE_MODEL,
        coach_model=s.STROKELAB_COACH_MODEL,
        coach_video=s.STROKELAB_COACH_VIDEO,
        coach_video_max_mb=s.STROKELAB_COACH_VIDEO_MAX_MB,
        segment_model=s.STROKELAB_COACH_SEGMENT_MODEL,
        max_coached_recoveries=s.STROKELAB_COACH_MAX_RECOVERIES,
        coach_call_delay_s=s.STROKELAB_COACH_INTER_VLM_DELAY_S,
    )

    def _strip_n() -> int:
        cap = cv2.VideoCapture(str(video_path))
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        total = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        cap.release()
        dur = (total / fps) if fps else 0.0
        return min(72, max(16, round(dur * 6)))  # hold ~6fps, capped for cost

    # key frames for the gate + holistic coach; denser strip (~6fps) for segmentation
    key_frames = await asyncio.to_thread(extract_key_frames, video_path, 8, 768)
    strip = await asyncio.to_thread(extract_key_frames, video_path, _strip_n(), 640)
    ctx = RunContext(
        frames=key_frames,
        strip=strip,
        profile=InputProfile.UNKNOWN,
        config=config,
        coaching=coach_context or CoachContext(),  # goal-aware grading/framing (§12)
        cache=dict(replay_cache or {}),  # replay paid VLM outputs on auto-retry
        video_path=str(video_path),  # lets pose_recovery decode its own dense frames
    )
    result = await run_pipeline(ctx, build_default_registry(), on_progress=on_progress)
    _apply_borderline_honesty(result)  # quiet the coach on a clip it can't read cleanly

    # Collect the frame bytes each finding cites, keyed "<component>:<index>".
    # holistic_coach cites KEY-frame indices; recovery_coach cites STRIP indices.
    evidence: dict[str, bytes] = {}
    for cr in result.results:
        src = key_frames if cr.component == "holistic_coach" else strip
        for finding in cr.findings:
            for ref in finding.evidence_frames:
                if 0 <= ref.index < len(src):
                    evidence[f"{cr.component}:{ref.index}"] = src[ref.index].jpeg

    # A thumbnail for EVERY detected near-arm recovery — not just the coached ones —
    # so every stroke-by-stroke tile shows its moment, coached or not. Grab the strip
    # frame nearest each recovery peak; key it by instance so the tile resolves it
    # (recovery_thumbnail:<instance_id>) independent of whether a finding cites a frame.
    if strip:
        for inst in ctx.instances:
            if inst.phase == Phase.RECOVERY and inst.arm == "near":
                nearest = min(strip, key=lambda f: abs(f.timestamp_s - inst.peak_s))
                evidence[f"recovery_thumbnail:{inst.instance_id}"] = nearest.jpeg

    # Render a shareable branded card per FIX finding (best-effort, gated by config).
    share_cards: dict[str, bytes] = {}
    if s.STROKELAB_COACH_SHARE_CARDS:
        from services.ai_service.coach.cards import render_share_card

        for cr in result.results:
            src = key_frames if cr.component == "holistic_coach" else strip
            for finding in cr.findings:
                if finding.severity != "fix" or not finding.evidence_frames:
                    continue
                ref = finding.evidence_frames[0]
                if not (0 <= ref.index < len(src)):
                    continue
                try:
                    share_cards[
                        f"{cr.component}:{ref.index}"
                    ] = await asyncio.to_thread(
                        render_share_card,
                        src[ref.index].jpeg,
                        finding.observation,
                        area=finding.area or "other",
                        timestamp_s=ref.timestamp_s,
                    )
                except Exception:
                    logger.warning("share-card render failed (%s)", finding.component)

    # Per-frame labels for the normalized swim_frame_labels table (queryable
    # corpus for analytics + fine-tuning). Joined to strip timestamps HERE so the
    # classification dataclass stays frame-metadata-free; the same labels also
    # live in cache["labels"] for $0 pipeline replay. Nothing is discarded.
    ts_by_index = {f.index: f.timestamp_s for f in strip}
    frame_labels = [
        {
            "frame_index": int(lab.get("index", -1)),
            "timestamp_s": float(ts_by_index.get(lab.get("index"), 0.0)),
            "phase": str(lab.get("phase", "indeterminate")),
            "arm": str(lab.get("arm", "none")),
            "subphase": str(lab.get("subphase", "") or ""),
            "conf": float(lab.get("conf", 0.0) or 0.0),
        }
        for lab in (ctx.cache.get("labels") or [])
        if int(lab.get("index", -1)) >= 0
    ]

    return {
        "engine_version": ver,
        "result": _enc(result),
        "cache": ctx.cache,
        "evidence": evidence,
        "share_cards": share_cards,
        "frame_labels": frame_labels,
    }


async def _write_partial(job_id: uuid.UUID, partial) -> None:
    """Persist a partial pipeline result mid-run so the result page can render
    section-by-section (progressive rendering). Best-effort — never raises into the
    pipeline. Writes ONLY while the job is still PROCESSING; the authoritative final
    write is _write_completed (which replaces this row). No evidence URLs yet, so
    findings render text-first and thumbnails fill in on completion."""
    from services.ai_service.pipeline.store import _enc

    try:
        async with AsyncSessionLocal() as session:
            job = await session.get(AnalysisJob, job_id)
            if job is None or job.status != AnalysisJobStatus.PROCESSING:
                return  # completed/failed/vanished — don't clobber the final row
            payload = {"result": _enc(partial), "partial": True}
            existing = await session.execute(
                select(AnalysisResult).where(AnalysisResult.job_id == job_id)
            )
            row = existing.scalar_one_or_none()
            if row is None:
                session.add(AnalysisResult(job_id=job_id, coach_result=payload))
            else:
                row.coach_result = payload
            await session.commit()
    except Exception:
        logger.debug("partial-progress write skipped for job %s", job_id, exc_info=True)


def _analysis_queue_name(source: AnalysisJobSource) -> str:
    return (
        PUBLIC_QUEUE_NAME if source == AnalysisJobSource.PUBLIC else MEMBER_QUEUE_NAME
    )


def _retry_payload(
    coach_payload: dict,
    *,
    attempt: int,
    delay_seconds: int,
    queued_at,
) -> dict:
    payload = {
        k: v
        for k, v in coach_payload.items()
        if k not in {"evidence", "share_cards", "frame_labels"}
    }
    next_retry_at = queued_at + timedelta(seconds=delay_seconds)
    payload["partial"] = True
    payload["retry"] = {
        "status": "retrying",
        "attempt": attempt,
        "max_attempts": len(_MAIN_COACH_AUTO_RETRY_DELAYS_SECONDS),
        "next_retry_at": next_retry_at.isoformat(),
        "message": "The AI coach hit a temporary error and is retrying automatically.",
    }
    result = payload.get("result")
    if isinstance(result, dict):
        meta = dict(result.get("meta") or {})
        meta["ai_coach_retry"] = payload["retry"]
        result["meta"] = meta
    return payload


async def _enqueue_analysis_retry(
    job_id: uuid.UUID,
    *,
    queue_name: str,
    delay_seconds: int,
) -> None:
    pool = await create_pool(get_redis_settings())
    try:
        await pool.enqueue_job(
            "task_analyze_swim_video",
            str(job_id),
            _queue_name=queue_name,
            _defer_by=timedelta(seconds=delay_seconds),
        )
    finally:
        await pool.close()


async def _mark_coach_retrying(
    job_id: uuid.UUID,
    coach_payload: dict,
    *,
    attempt: int,
    delay_seconds: int,
    usage_events: list[dict] | None = None,
) -> None:
    queued_at = utc_now()
    queue_name = MEMBER_QUEUE_NAME
    async with AsyncSessionLocal() as session:
        job = await session.get(AnalysisJob, job_id)
        if job is None:
            return
        queue_name = _analysis_queue_name(job.source)

        if usage_events is not None:
            run_usage = await record_usage_events(
                session,
                events=usage_events,
                request_type="strokelab_analysis",
                requesting_service="ai_service",
            )
            coach_payload["provider_usage"] = {
                "run": run_usage,
                "gemini_quota": await gemini_quota_estimate(session),
            }

        job.status = AnalysisJobStatus.PENDING
        job.error_message = None
        job.completed_at = None

        payload = _retry_payload(
            coach_payload,
            attempt=attempt,
            delay_seconds=delay_seconds,
            queued_at=queued_at,
        )
        existing = await session.execute(
            select(AnalysisResult).where(AnalysisResult.job_id == job_id)
        )
        row = existing.scalar_one_or_none()
        if row is None:
            session.add(AnalysisResult(job_id=job_id, coach_result=payload))
        else:
            row.coach_result = payload
        await session.commit()

    try:
        await _enqueue_analysis_retry(
            job_id,
            queue_name=queue_name,
            delay_seconds=delay_seconds,
        )
    except Exception:
        logger.exception("Failed to enqueue AI coach retry for job %s", job_id)
        await _mark_failed(job_id, "temporarily_unavailable")


async def _write_completed(
    job_id: uuid.UUID,
    detected_stroke: str,
    coach_payload: dict | None = None,
    frame_labels: list[dict] | None = None,
    usage_events: list[dict] | None = None,
) -> None:
    ready_email: tuple[str, str, dict | None] | None = None
    async with AsyncSessionLocal() as session:
        job = await session.get(AnalysisJob, job_id)
        if job is None:
            logger.warning(
                "Stroke Lab job %s disappeared before completion write", job_id
            )
            return
        job.status = AnalysisJobStatus.COMPLETED
        job.completed_at = utc_now()
        # The annotated pose-overlay video is retired with the pose engine; the
        # result page falls back to the original clip.
        job.annotated_video_storage_path = None

        provider_usage = None
        if usage_events is not None:
            run_usage = await record_usage_events(
                session,
                events=usage_events,
                request_type="strokelab_analysis",
                requesting_service="ai_service",
            )
            provider_usage = {
                "run": run_usage,
                "gemini_quota": await gemini_quota_estimate(session),
            }
            if coach_payload is not None:
                coach_payload["provider_usage"] = provider_usage

        # Replace any prior result row (idempotency for re-runs).
        existing = await session.execute(
            select(AnalysisResult).where(AnalysisResult.job_id == job_id)
        )
        prior = existing.scalar_one_or_none()
        if prior is not None:
            await session.delete(prior)
            await session.flush()

        # Coach-primary write: detected_stroke echoes the requested stroke; every
        # pose/metrics column is honestly NULL (the pose pass is gone).
        result = AnalysisResult(
            job_id=job_id,
            detected_stroke=detected_stroke,
            pose_detection_rate=None,
            frames_total=None,
            frames_with_pose=None,
            stroke_rate_spm=None,
            body_roll_proxy_degrees=None,
            breath_count_left=None,
            breath_count_right=None,
            breath_balance_left_ratio=None,
            summary_text=None,
            observations=None,
            tracking_gaps=None,
            pipeline_config=None,
            raw_metrics=None,
            coach_result=coach_payload,  # the stored coach run (gate/findings/cache)
        )
        session.add(result)

        # Normalized per-frame labels (queryable corpus for analytics/fine-tuning).
        # Replace any prior rows for idempotency on re-run, then bulk-insert.
        if frame_labels:
            await session.execute(
                delete(SwimFrameLabel).where(SwimFrameLabel.job_id == job_id)
            )
            ver = (coach_payload or {}).get("engine_version")
            session.add_all(
                [
                    SwimFrameLabel(
                        job_id=job_id,
                        frame_index=fl["frame_index"],
                        timestamp_s=fl["timestamp_s"],
                        phase=fl["phase"],
                        arm=fl["arm"],
                        subphase=fl["subphase"],
                        conf=fl["conf"],
                        engine_version=ver,
                    )
                    for fl in frame_labels
                ]
            )
        # Public jobs: spend the reserved credit in the SAME transaction as the
        # completion (design §6.1). Member jobs have no reservation.
        if job.source == AnalysisJobSource.PUBLIC and job.guest_email:
            await consume_reservation(session, raw_email=job.guest_email, job_id=job_id)
            # "Ready" email: set the single-send guard in THIS transaction; the
            # actual send is best-effort, after commit (design §8.1).
            if job.email_sent_at is None:
                job.email_sent_at = utc_now()
                ready_email = (job.guest_email, job.guest_token or "", provider_usage)
        await session.commit()

    if ready_email is not None:
        await send_ready_email(
            job_id, ready_email[0], ready_email[1], provider_usage=ready_email[2]
        )


def _failure_reason(exc: Exception) -> str:
    """Map a pipeline exception to a STABLE, user-mappable reason (the frontend turns
    these into friendly copy + a retry). Never let a raw traceback reach the user."""
    name, msg = type(exc).__name__, str(exc).lower()
    if any(s in name for s in ("ServiceUnavailable", "Timeout", "RateLimit")) or any(
        s in msg
        for s in (
            "503",
            "unavailable",
            "overloaded",
            "high demand",
            "rate limit",
            "ratelimit",
            "timed out",
            "timeout",
        )
    ):
        return "temporarily_unavailable"  # transient — a retry will likely work
    if any(
        s in msg
        for s in (
            "api key",
            "api_key",
            "authentication",
            "unauthorized",
            "permission",
            "quota",
            "insufficient_quota",
            "billing",
        )
    ):
        return "coach_unavailable"  # config/quota — a retry won't help
    if any(
        s in msg
        for s in ("could not read", "unreadable", "decode", "corrupt", "no video")
    ):
        return "video_unreadable"
    return "analysis_error"


async def _mark_failed(
    job_id: uuid.UUID, error_message: str, usage_events: list[dict] | None = None
) -> None:
    failed_email: tuple[str, dict | None] | None = None
    async with AsyncSessionLocal() as session:
        job = await session.get(AnalysisJob, job_id)
        if job is None:
            return
        provider_usage = None
        if usage_events is not None:
            run_usage = await record_usage_events(
                session,
                events=usage_events,
                request_type="strokelab_analysis",
                requesting_service="ai_service",
            )
            provider_usage = {
                "run": run_usage,
                "gemini_quota": await gemini_quota_estimate(session),
            }
        job.status = AnalysisJobStatus.FAILED
        job.completed_at = utc_now()
        # Cap error_message at a sane length so a giant traceback can't
        # fill the column on a stuck job loop.
        job.error_message = (error_message or "")[:2000]
        # Public jobs: refund the reserved credit in the SAME transaction as the
        # failure (design §6.1) — a failed analysis never costs a credit.
        if job.source == AnalysisJobSource.PUBLIC and job.guest_email:
            await refund_reservation(session, raw_email=job.guest_email, job_id=job_id)
            # "Couldn't analyze" email — single-send guard set here, sent
            # best-effort after commit (design §8.6).
            if job.email_sent_at is None:
                job.email_sent_at = utc_now()
                failed_email = (job.guest_email, provider_usage)
        await session.commit()

    if failed_email is not None:
        await send_failed_email(job_id, failed_email[0], provider_usage=failed_email[1])
