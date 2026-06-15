"""ARQ task: run Stroke Lab analysis on one queued job.

Lifecycle:
  1. Worker picks up the job, loads the AnalysisJob row.
  2. Status flips to PROCESSING + started_at set.
  3. Upload pulled from Supabase to a tempfile.
  4. Pipeline runs (CPU-bound, offloaded to a thread inside run_analysis).
  5. Annotated mp4 uploaded to the annotated bucket.
  6. AnalysisResult row written.
  7. Job status flips to COMPLETED + completed_at set.
  8. Tempfiles cleaned up.

Any exception flips the job to FAILED with the error message captured;
the caller will see it on the next GET /ai/analyze/{job_id}.
"""

from __future__ import annotations

import asyncio
import tempfile
import uuid
from pathlib import Path

from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from libs.db.config import AsyncSessionLocal
from sqlalchemy import select

from services.ai_service.analysis import (
    DEFAULT_PIPELINE_CONFIG,
    AnalysisReport,
    PipelineConfig,
    run_analysis,
)
from services.ai_service.analysis.storage import (
    UPLOADS_BUCKET,
    temp_file_from_storage,
    upload_annotated_video,
    upload_guest_annotated_video,
)
from services.ai_service.models import (
    AnalysisJob,
    AnalysisJobSource,
    AnalysisJobStatus,
    AnalysisResult,
)

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

    # 2. Run pipeline against a tempfile download. Wrap the sync ctx manager
    # in to_thread so we don't block the loop on the network read.
    try:
        annotated_local: Path | None = None
        with tempfile.TemporaryDirectory(prefix="strokelab_") as workdir:
            workdir_path = Path(workdir)
            annotated_local = workdir_path / f"{job_uuid}.annotated.mp4"

            # Pull the upload to a tempfile. temp_file_from_storage is a
            # sync ctx manager; emulate with a manual download into our
            # workdir for cleanliness.
            uploaded_local = await _download_upload(video_storage_path, workdir_path)

            report: AnalysisReport = await run_analysis(
                uploaded_local,
                annotated_local,
                stroke_type=stroke_type,
                config=_pipeline_config_from_env(),
            )

            # 3. Upload annotated mp4. Guest jobs have no member_auth_id, so
            # they key under guest/{guest_token}/... like their original upload.
            if source == AnalysisJobSource.PUBLIC:
                annotated_key = await upload_guest_annotated_video(
                    guest_token, job_uuid, annotated_local
                )
            else:
                annotated_key = await upload_annotated_video(
                    member_auth_id, job_uuid, annotated_local
                )

        # 4. Persist result row + flip status
        await _write_completed(job_uuid, annotated_key, report)
        logger.info(
            "Stroke Lab job %s completed: pose=%.3f spm=%s",
            job_id,
            report.pose_detection_rate,
            report.stroke_rate_spm,
        )
        return {
            "status": "completed",
            "pose_detection_rate": report.pose_detection_rate,
            "stroke_rate_spm": report.stroke_rate_spm,
        }
    except Exception as exc:  # pragma: no cover — failure path
        logger.exception("Stroke Lab job %s failed", job_id)
        await _mark_failed(job_uuid, str(exc))
        return {"status": "failed", "error": str(exc)}


# ── Internal helpers ──────────────────────────────────────────────


def _pipeline_config_from_env() -> PipelineConfig:
    """Production config: defaults to the kill-gate winner. Override
    individual knobs via env vars without redeploying."""
    import os

    base = DEFAULT_PIPELINE_CONFIG
    return PipelineConfig(
        pose_model_variant=os.environ.get(
            "STROKELAB_POSE_MODEL", base.pose_model_variant
        ),
        max_inference_side=int(
            os.environ.get("STROKELAB_MAX_SIDE", base.max_inference_side)
        ),
        use_yolo=os.environ.get("STROKELAB_USE_YOLO", "1") == "1",
        yolo_conf_threshold=float(
            os.environ.get("STROKELAB_YOLO_CONF", base.yolo_conf_threshold)
        ),
        frame_stride=int(os.environ.get("STROKELAB_FRAME_STRIDE", base.frame_stride)),
        enable_summary=os.environ.get("STROKELAB_ENABLE_SUMMARY", "1") == "1",
    )


async def _download_upload(storage_path: str, workdir: Path) -> Path:
    """Pull the user's upload into the worker's temp workdir."""

    def _do_download() -> Path:
        with temp_file_from_storage(UPLOADS_BUCKET, storage_path) as tmp:
            dest = workdir / Path(storage_path).name
            dest.write_bytes(tmp.read_bytes())
            return dest

    return await asyncio.to_thread(_do_download)


async def _write_completed(
    job_id: uuid.UUID,
    annotated_storage_path: str,
    report: AnalysisReport,
) -> None:
    async with AsyncSessionLocal() as session:
        job = await session.get(AnalysisJob, job_id)
        if job is None:
            logger.warning(
                "Stroke Lab job %s disappeared before completion write", job_id
            )
            return
        job.status = AnalysisJobStatus.COMPLETED
        job.completed_at = utc_now()
        job.annotated_video_storage_path = annotated_storage_path

        # Replace any prior result row (idempotency for re-runs).
        existing = await session.execute(
            select(AnalysisResult).where(AnalysisResult.job_id == job_id)
        )
        prior = existing.scalar_one_or_none()
        if prior is not None:
            await session.delete(prior)
            await session.flush()

        result = AnalysisResult(
            job_id=job_id,
            detected_stroke=report.detected_stroke,
            pose_detection_rate=report.pose_detection_rate,
            frames_total=report.frames_total,
            frames_with_pose=report.frames_with_pose,
            stroke_rate_spm=report.stroke_rate_spm,
            body_roll_proxy_degrees=report.body_roll_proxy_degrees,
            breath_count_left=report.breath_count_left,
            breath_count_right=report.breath_count_right,
            breath_balance_left_ratio=report.breath_balance_left_ratio,
            summary_text=report.summary_text,
            observations=report.observations,
            tracking_gaps=report.tracking_gaps,
            pipeline_config=report.config_snapshot,
            raw_metrics=report.raw_metrics,
        )
        session.add(result)
        await session.commit()


async def _mark_failed(job_id: uuid.UUID, error_message: str) -> None:
    async with AsyncSessionLocal() as session:
        job = await session.get(AnalysisJob, job_id)
        if job is None:
            return
        job.status = AnalysisJobStatus.FAILED
        job.completed_at = utc_now()
        # Cap error_message at a sane length so a giant traceback can't
        # fill the column on a stuck job loop.
        job.error_message = (error_message or "")[:2000]
        await session.commit()
