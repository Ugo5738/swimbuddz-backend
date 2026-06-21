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
from pathlib import Path

from libs.common.logging import get_logger
from libs.db.config import AsyncSessionLocal
from sqlalchemy import select

from services.ai_service.models import (
    AnalysisJob,
    AnalysisJobSource,
    AnalysisResult,
)

logger = get_logger(__name__)


def _strip_count(video_path: Path) -> int:
    import cv2

    cap = cv2.VideoCapture(str(video_path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    cap.release()
    dur = (total / fps) if fps else 0.0
    return min(72, max(16, round(dur * 6)))


async def inspect_instance(job_id: str, aspect: str, instance_id: int) -> dict:
    """Coach one instance and persist it. Returns a small status dict for the queue."""
    from libs.common.config import get_settings

    from services.ai_service.analysis.storage import upload_evidence_frames
    from services.ai_service.coach.frames import extract_key_frames
    from services.ai_service.pipeline.defaults import aspect_component
    from services.ai_service.pipeline.store import _enc
    from services.ai_service.pipeline.types import (
        CoachContext,
        Instance,
        InputProfile,
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
        return {"status": "no_instance"}

    s = get_settings()
    config = PipelineConfig(
        gate_model=s.STROKELAB_COACH_GATE_MODEL,
        coach_model=s.STROKELAB_COACH_MODEL,
        segment_model=s.STROKELAB_COACH_SEGMENT_MODEL,
    )

    # 2. Re-extract the strip (cv2) and coach the one instance (gate/segment $0
    #    because the cache replays; only this aspect's VLM call is paid).
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
        )
        finding = await comp_cls().coach_instance(ctx, instance_id)
        if finding is None:
            return {"status": "no_finding"}

        # 3. Upload the evidence frame(s) this finding cites.
        prefix = (
            f"guest/{guest_token}"
            if source == AnalysisJobSource.PUBLIC
            else str(member_auth_id)
        )
        evidence = {
            f"{finding.component}:{ref.index}": strip[ref.index].jpeg
            for ref in finding.evidence_frames
            if 0 <= ref.index < len(strip)
        }
        evidence_keys: dict = {}
        if evidence:
            try:
                evidence_keys = await upload_evidence_frames(prefix, job_uuid, evidence)
            except Exception:
                logger.warning("inspect: evidence upload failed for %s", job_id)

    enc = _enc(finding)

    # 4. Persist: append the finding into coach_result (replace any prior read of
    #    the same instance/aspect — idempotent), merge cache + evidence keys.
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
            (r for r in results_list if r.get("component") == comp_cls.name), None
        )
        if bucket is None:
            bucket = {
                "component": comp_cls.name,
                "findings": [],
                "cost_usd": 0.0,
                "error": None,
                "meta": {},
            }
            results_list.append(bucket)
        bucket["findings"] = [
            f
            for f in bucket.get("findings", [])
            if not (f.get("instance_id") == instance_id and f.get("area") == aspect)
        ]
        bucket["findings"].append(enc)
        result_obj["results"] = results_list
        cr["result"] = result_obj
        cr["cache"] = ctx.cache  # now holds the new instance's verdict for $0 re-view
        merged_keys = dict(cr.get("evidence_keys") or {})
        merged_keys.update(evidence_keys)
        cr["evidence_keys"] = merged_keys
        row.coach_result = cr  # reassign → SQLAlchemy marks the JSON column dirty
        await session.commit()

    logger.info("inspect: coached %s #%s for job %s", aspect, instance_id, job_id)
    return {"status": "inspected", "aspect": aspect, "instance_id": instance_id}
