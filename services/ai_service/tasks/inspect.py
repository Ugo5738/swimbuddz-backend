"""ARQ task: coach ONE stored instance on demand — the per-stroke drilldown.

Replays the stored run from ``coach_result['cache']`` (gate/segment cost $0),
re-extracts the clip's frames (cv2 — worker only, the API is cv2-free), coaches the
single requested aspect+instance with one VLM call, and persists the new Finding
back into ``coach_result`` + uploads its evidence. Idempotent: a re-inspect of an
already-coached instance replays the cache → $0 and overwrites the same slot.

Billing is COMPED today (``STROKELAB_INSPECT_BILLING`` off): while the drilldown is
in preview-unlock mode the per-stroke count isn't accuracy-validated, so charging a
credit for it would be dishonest. Real pay-per-inspect (comp-first, then 1 credit)
lands when the accuracy gate is met and the flag is flipped.
"""

from __future__ import annotations

import asyncio
import tempfile
import uuid
from datetime import timedelta
from pathlib import Path

from arq import create_pool
from sqlalchemy import select

from libs.common.arq_config import get_redis_settings
from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from libs.db.config import AsyncSessionLocal
from services.ai_service.constants import MEMBER_QUEUE_NAME, PUBLIC_QUEUE_NAME
from services.ai_service.models import AnalysisJob, AnalysisJobSource, AnalysisResult
from services.ai_service.services.inspect_status import set_inspect_status

logger = get_logger(__name__)

_MAX_INSPECT_ATTEMPTS = 6
_INSPECT_RETRY_DELAYS_SECONDS = (60, 180, 600, 1800, 3600)


def _strip_count(video_path: Path) -> int:
    import cv2

    cap = cv2.VideoCapture(str(video_path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    cap.release()
    dur = (total / fps) if fps else 0.0
    return min(72, max(16, round(dur * 6)))


def _queue_name(source: AnalysisJobSource) -> str:
    return (
        PUBLIC_QUEUE_NAME if source == AnalysisJobSource.PUBLIC else MEMBER_QUEUE_NAME
    )


def _failure_reason(exc: Exception) -> str:
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
        return "temporarily_unavailable"
    return "inspect_error"


def _retry_delay(attempt: int) -> int:
    idx = max(0, min(attempt - 1, len(_INSPECT_RETRY_DELAYS_SECONDS) - 1))
    return _INSPECT_RETRY_DELAYS_SECONDS[idx]


async def _write_status(
    job_uuid: uuid.UUID,
    aspect: str,
    instance_id: int,
    *,
    status: str,
    attempt: int,
    message: str,
    next_retry_at=None,
    queue_depth: int | None = None,
    error_reason: str | None = None,
) -> dict:
    async with AsyncSessionLocal() as session:
        payload = await set_inspect_status(
            session,
            job_id=job_uuid,
            aspect=aspect,
            instance_id=instance_id,
            status=status,
            attempt=attempt,
            message=message,
            next_retry_at=next_retry_at,
            queue_depth=queue_depth,
            error_reason=error_reason,
        )
        await session.commit()
        return payload


async def _schedule_retry(
    job_uuid: uuid.UUID,
    aspect: str,
    instance_id: int,
    *,
    source: AnalysisJobSource,
    attempt: int,
    reason: str,
) -> dict:
    next_attempt = attempt + 1
    delay = _retry_delay(attempt)
    next_retry_at = utc_now() + timedelta(seconds=delay)
    message = (
        "The video coach is busy, so this stroke is queued for an automatic retry."
    )
    await _write_status(
        job_uuid,
        aspect,
        instance_id,
        status="retrying",
        attempt=attempt,
        message=message,
        next_retry_at=next_retry_at,
        error_reason=reason,
    )
    queue_name = _queue_name(source)
    pool = await create_pool(get_redis_settings())
    try:
        await pool.enqueue_job(
            "task_inspect_instance",
            str(job_uuid),
            aspect,
            instance_id,
            next_attempt,
            _queue_name=queue_name,
            _defer_by=timedelta(seconds=delay),
        )
        depth = int(await pool.zcard(queue_name))
    except Exception:
        await _write_status(
            job_uuid,
            aspect,
            instance_id,
            status="failed",
            attempt=attempt,
            message="Could not queue the automatic retry. Try again.",
            error_reason="queue_failed",
        )
        raise
    finally:
        await pool.close()
    payload = await _write_status(
        job_uuid,
        aspect,
        instance_id,
        status="retrying",
        attempt=attempt,
        message=message,
        next_retry_at=next_retry_at,
        queue_depth=depth,
        error_reason=reason,
    )
    return {
        "status": "retrying",
        "attempt": attempt,
        "next_attempt": next_attempt,
        "next_retry_at": payload.get("next_retry_at"),
        "reason": reason,
    }


async def inspect_instance(
    job_id: str, aspect: str, instance_id: int, attempt: int = 1
) -> dict:
    """Coach one instance and persist it. Returns a small status dict for the queue."""
    from libs.common.config import get_settings
    from services.ai_service.analysis.storage import upload_evidence_frames
    from services.ai_service.coach.frames import extract_key_frames
    from services.ai_service.pipeline.defaults import aspect_component
    from services.ai_service.pipeline.store import _enc
    from services.ai_service.pipeline.types import (
        CoachContext,
        InputProfile,
        Instance,
        Phase,
        PipelineConfig,
        RunContext,
    )
    from services.ai_service.tasks.analyze import _download_upload

    job_uuid = uuid.UUID(job_id)
    comp_cls = aspect_component(aspect)
    if comp_cls is None:
        return {"status": "bad_aspect"}

    # 1. Load the stored run.
    async with AsyncSessionLocal() as session:
        job = await session.get(AnalysisJob, job_uuid)
        rs = await session.execute(
            select(AnalysisResult).where(AnalysisResult.job_id == job_uuid)
        )
        result = rs.scalar_one_or_none()
        if job is None or result is None or not result.coach_result:
            return {"status": "missing"}
        coach = dict(result.coach_result)
        source = job.source
        guest_token = job.guest_token
        member_auth_id = job.member_auth_id
        video_storage_path = job.video_storage_path
        coach_context = CoachContext(
            discipline=job.discipline,
            level=job.level,
            focus_area=job.focus_area,
            goal_text=job.goal_text,
        )
        await set_inspect_status(
            session,
            job_id=job_uuid,
            aspect=aspect,
            instance_id=instance_id,
            status="processing",
            attempt=attempt,
            message="The video coach is reading this stroke now.",
        )
        await session.commit()

    cache = coach.get("cache") or {}
    instances = [
        Instance(
            phase=Phase(d["phase"]),
            instance_id=int(d["instance_id"]),
            arm=d.get("arm", "none"),
            start_s=float(d["start_s"]),
            end_s=float(d["end_s"]),
            peak_s=float(d["peak_s"]),
            peak_index=int(d.get("peak_index", 0)),
            confidence=float(d.get("confidence", 0.0)),
        )
        for d in (cache.get("instances") or [])
    ]
    if not any(i.instance_id == instance_id for i in instances):
        await _write_status(
            job_uuid,
            aspect,
            instance_id,
            status="failed",
            attempt=attempt,
            message="That stroke instance is no longer available.",
            error_reason="no_instance",
        )
        return {"status": "failed", "error": "no_instance"}

    s = get_settings()
    config = PipelineConfig(
        gate_model=s.STROKELAB_COACH_GATE_MODEL,
        coach_model=s.STROKELAB_COACH_MODEL,
        segment_model=s.STROKELAB_COACH_SEGMENT_MODEL,
        # The chunk coach cuts a video clip for the chosen stroke, just like the
        # free flow — so on-demand gets the same motion-aware multi-aspect read.
        coach_video=s.STROKELAB_COACH_VIDEO,
        coach_video_max_mb=s.STROKELAB_COACH_VIDEO_MAX_MB,
    )

    # 2. Re-extract the strip (cv2) and coach the one instance (gate/segment $0
    #    because the cache replays; only this aspect's VLM call is paid).
    try:
        with tempfile.TemporaryDirectory(prefix="strokelab_inspect_") as workdir:
            wd = Path(workdir)
            video = await _download_upload(video_storage_path, wd)
            strip = await asyncio.to_thread(
                extract_key_frames, video, _strip_count(video), 640
            )
            ctx = RunContext(
                frames=strip,
                strip=strip,
                profile=InputProfile.UNKNOWN,
                config=config,
                coaching=coach_context,
                instances=instances,
                cache=cache,
                video_path=str(video),  # lets the chunk coach cut this stroke's clip
            )
            # coach_instances returns a LIST: a single-aspect component yields one
            # finding; the chunk coach yields the full multi-aspect read.
            findings = await comp_cls().coach_instances(ctx, instance_id)
            if not findings:
                if attempt < _MAX_INSPECT_ATTEMPTS:
                    return await _schedule_retry(
                        job_uuid,
                        aspect,
                        instance_id,
                        source=source,
                        attempt=attempt,
                        reason="no_finding",
                    )
                await _write_status(
                    job_uuid,
                    aspect,
                    instance_id,
                    status="failed",
                    attempt=attempt,
                    message="The coach could not produce a reliable read for this stroke.",
                    error_reason="no_finding",
                )
                return {"status": "failed", "error": "no_finding"}

            # 3. Upload the evidence frame(s) every finding cites.
            prefix = (
                f"guest/{guest_token}"
                if source == AnalysisJobSource.PUBLIC
                else str(member_auth_id)
            )
            evidence = {
                f"{f.component}:{ref.index}": strip[ref.index].jpeg
                for f in findings
                for ref in f.evidence_frames
                if 0 <= ref.index < len(strip)
            }
            evidence_keys: dict = {}
            if evidence:
                try:
                    evidence_keys = await upload_evidence_frames(
                        prefix, job_uuid, evidence
                    )
                except Exception:
                    logger.warning("inspect: evidence upload failed for %s", job_id)
    except Exception as exc:
        reason = _failure_reason(exc)
        if attempt < _MAX_INSPECT_ATTEMPTS and reason == "temporarily_unavailable":
            return await _schedule_retry(
                job_uuid,
                aspect,
                instance_id,
                source=source,
                attempt=attempt,
                reason=reason,
            )
        await _write_status(
            job_uuid,
            aspect,
            instance_id,
            status="failed",
            attempt=attempt,
            message="The video coach could not finish this stroke. Try again.",
            error_reason=reason,
        )
        logger.exception(
            "inspect: failed %s #%s for job %s", aspect, instance_id, job_id
        )
        return {"status": "failed", "error": reason}

    encs = [_enc(f) for f in findings]
    component_name = comp_cls.name

    # 4. Persist into coach_result: replace ALL of this component's prior reads of
    #    this instance (idempotent — a single-aspect read swaps one finding, a chunk
    #    read swaps the whole stroke's multi-aspect set), then add the fresh read(s).
    async with AsyncSessionLocal() as session:
        rs = await session.execute(
            select(AnalysisResult).where(AnalysisResult.job_id == job_uuid)
        )
        row = rs.scalar_one_or_none()
        if row is None:
            return {"status": "missing"}
        cr = dict(row.coach_result or {})
        result_obj = dict(cr.get("result") or {})
        results_list = list(result_obj.get("results") or [])
        bucket = next(
            (r for r in results_list if r.get("component") == component_name), None
        )
        if bucket is None:
            bucket = {
                "component": component_name,
                "findings": [],
                "cost_usd": 0.0,
                "error": None,
                "meta": {},
            }
            results_list.append(bucket)
        bucket["findings"] = [
            f for f in bucket.get("findings", []) if f.get("instance_id") != instance_id
        ]
        bucket["findings"].extend(encs)
        result_obj["results"] = results_list
        cr["result"] = result_obj
        cr["cache"] = ctx.cache  # now holds the new instance's verdict for $0 re-view
        merged_keys = dict(cr.get("evidence_keys") or {})
        merged_keys.update(evidence_keys)
        cr["evidence_keys"] = merged_keys
        row.coach_result = cr  # reassign → SQLAlchemy marks the JSON column dirty
        await set_inspect_status(
            session,
            job_id=job_uuid,
            aspect=aspect,
            instance_id=instance_id,
            status="completed",
            attempt=attempt,
            message="This stroke read is ready.",
        )
        await session.commit()

    logger.info("inspect: coached %s #%s for job %s", aspect, instance_id, job_id)
    return {"status": "completed", "aspect": aspect, "instance_id": instance_id}
