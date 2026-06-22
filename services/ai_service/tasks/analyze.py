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
from pathlib import Path

from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from libs.db.config import AsyncSessionLocal
from sqlalchemy import delete, select

from services.ai_service.pipeline.types import CoachContext  # import-light (no cv2)
from services.ai_service.analysis.storage import (
    UPLOADS_BUCKET,
    temp_file_from_storage,
    upload_evidence_frames,
)
from services.ai_service.constants import PUBLIC_MAX_DURATION_SECONDS
from services.ai_service.models import (
    AnalysisJob,
    AnalysisJobSource,
    AnalysisJobStatus,
    AnalysisResult,
    SwimFrameLabel,
)
from services.ai_service.services.credit_ops import (
    consume_reservation,
    refund_reservation,
)
from services.ai_service.services.notify import send_failed_email, send_ready_email

logger = get_logger(__name__)


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
        # Goal-aware coaching context (§12) — captured while the session is open.
        coach_context = CoachContext(
            discipline=job.discipline,
            level=job.level,
            focus_area=job.focus_area,
            goal_text=job.goal_text,
        )

    # 2. The VLM coach is the analysis now — PRIMARY + REQUIRED. A coach
    # exception propagates to the failure path (FAILED + refund); a gate refusal
    # is handled explicitly below.
    try:
        coach_payload: dict | None = None
        frame_labels: list[dict] | None = None
        with tempfile.TemporaryDirectory(prefix="strokelab_") as workdir:
            workdir_path = Path(workdir)

            # Pull the upload to a tempfile (temp_file_from_storage is sync; we
            # download into our workdir for cleanliness).
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
                    await _mark_failed(job_uuid, "too_long")
                    return {"status": "failed", "error": "too_long"}

            # Run the coach (gate → classify → per-instance + holistic). A raise
            # here propagates to the failure path below.
            coach_payload = await _run_coach_pipeline(uploaded_local, coach_context)
            if coach_payload is None:
                # Coach disabled (STROKELAB_ENABLE_COACH off) — there is no
                # fallback engine now, so the job can't produce a result.
                logger.error("Coach disabled but job %s reached the worker", job_id)
                await _mark_failed(job_uuid, "coach_unavailable")
                return {"status": "failed", "error": "coach_unavailable"}

            # Gate refusal: an above-water/side-on clip we can't read. Refund and
            # surface the "film a side-on clip" guidance — we won't guess.
            if _coach_refused(coach_payload):
                logger.info("Stroke Lab job %s refused by the gate", job_id)
                await _mark_failed(job_uuid, "could_not_track")
                return {"status": "refused"}

            # Non-refused but empty: a coach component errored and was swallowed,
            # leaving no real findings. Don't charge for a blank read — refund and
            # give the same "film a clearer clip" guidance.
            if not _coach_has_content(coach_payload):
                logger.warning("Stroke Lab job %s produced no coaching content", job_id)
                await _mark_failed(job_uuid, "could_not_track")
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
        await _write_completed(job_uuid, stroke_type, coach_payload, frame_labels)
        logger.info("Stroke Lab job %s completed (coach-primary)", job_id)
        return {"status": "completed"}
    except Exception as exc:  # pragma: no cover — failure path
        logger.exception("Stroke Lab job %s failed", job_id)
        await _mark_failed(job_uuid, str(exc))
        return {"status": "failed", "error": str(exc)}


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
        for f in cr.get("findings") or []:
            if f.get("available", True) and f.get("severity") in (
                "fix",
                "strength",
                "info",
            ):
                return True
    return False


async def _download_upload(storage_path: str, workdir: Path) -> Path:
    """Pull the user's upload into the worker's temp workdir."""

    def _do_download() -> Path:
        with temp_file_from_storage(UPLOADS_BUCKET, storage_path) as tmp:
            dest = workdir / Path(storage_path).name
            dest.write_bytes(tmp.read_bytes())
            return dest

    return await asyncio.to_thread(_do_download)


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


async def _run_coach_pipeline(
    video_path: Path, coach_context: CoachContext | None = None
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
        segment_model=s.STROKELAB_COACH_SEGMENT_MODEL,
        max_coached_recoveries=s.STROKELAB_COACH_MAX_RECOVERIES,
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
        cache={},  # collect the paid VLM outputs so we can re-derive for free
    )
    result = await run_pipeline(ctx, build_default_registry())

    # Collect the frame bytes each finding cites, keyed "<component>:<index>".
    # holistic_coach cites KEY-frame indices; recovery_coach cites STRIP indices.
    evidence: dict[str, bytes] = {}
    for cr in result.results:
        src = key_frames if cr.component == "holistic_coach" else strip
        for finding in cr.findings:
            for ref in finding.evidence_frames:
                if 0 <= ref.index < len(src):
                    evidence[f"{cr.component}:{ref.index}"] = src[ref.index].jpeg

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


async def _write_completed(
    job_id: uuid.UUID,
    detected_stroke: str,
    coach_payload: dict | None = None,
    frame_labels: list[dict] | None = None,
) -> None:
    ready_email: tuple[str, str] | None = None
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
                ready_email = (job.guest_email, job.guest_token or "")
        await session.commit()

    if ready_email is not None:
        await send_ready_email(job_id, ready_email[0], ready_email[1])


async def _mark_failed(job_id: uuid.UUID, error_message: str) -> None:
    failed_email: str | None = None
    async with AsyncSessionLocal() as session:
        job = await session.get(AnalysisJob, job_id)
        if job is None:
            return
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
                failed_email = job.guest_email
        await session.commit()

    if failed_email is not None:
        await send_failed_email(job_id, failed_email)
