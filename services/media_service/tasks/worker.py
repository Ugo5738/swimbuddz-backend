"""ARQ worker for media service background tasks.

Processes video uploads asynchronously: transcode, thumbnail, metadata extraction.
Run with: arq services.media_service.tasks.worker.WorkerSettings
"""

from libs.common.arq_config import get_redis_settings
from libs.common.logging import get_logger

logger = get_logger(__name__)


# ── Wrapper functions (ARQ requires top-level async callables) ──


async def task_process_video(
    ctx: dict,
    media_item_id: str,
    original_file_url: str,
    bucket_type_value: str,
):
    """Process an uploaded video: transcode, thumbnail, metadata."""
    from services.media_service.tasks import process_video_upload

    logger.info("Running: process_video_upload for %s", media_item_id)
    return await process_video_upload(
        media_item_id, original_file_url, bucket_type_value
    )


async def task_apply_audio(
    ctx: dict,
    media_item_id: str,
    video_url: str,
    audio_url: str,
    volume_mix: float = 1.0,
    audio_start_offset: float = 0.0,
):
    """Apply audio overlay to a video: download, mix, re-upload."""
    from services.media_service.tasks import apply_audio_overlay

    logger.info("Running: apply_audio_overlay for %s", media_item_id)
    return await apply_audio_overlay(
        media_item_id, video_url, audio_url, volume_mix, audio_start_offset
    )


# ── Worker configuration ──


class WorkerSettings:
    """ARQ worker settings for media processing."""

    redis_settings = get_redis_settings()
    queue_name = "arq:media"

    # Video transcoding is CPU-intensive — limit concurrent jobs
    max_jobs = 2

    # Long timeout for large video transcoding (15 minutes)
    job_timeout = 900

    # Register task functions
    functions = [task_process_video, task_apply_audio]

    # No scheduled cron jobs — all tasks are on-demand
    cron_jobs = []
