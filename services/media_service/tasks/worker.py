"""ARQ worker for media service background tasks.

Processes video uploads asynchronously: transcode, thumbnail, metadata extraction.
Run with: arq services.media_service.tasks.worker.WorkerSettings
"""

from arq import cron

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


async def task_build_vault_export(ctx: dict, export_id: str):
    """Stream selected originals into a ZIP and put it back in private S3."""
    from services.media_service.tasks.vault_exports import build_vault_export

    return await build_vault_export(export_id)


async def task_build_vault_preview(ctx: dict, media_item_id: str):
    """Generate a review proxy only after a curator explicitly requests it."""
    from services.media_service.tasks.vault_previews import build_vault_preview

    return await build_vault_preview(media_item_id)


async def task_cleanup_vault_exports(ctx: dict):
    """Expire generated ZIPs; never modifies vault originals."""
    from services.media_service.tasks.vault_exports import cleanup_expired_vault_exports

    return await cleanup_expired_vault_exports()


async def task_reconcile_vault_bandwidth(ctx: dict):
    """Replace vault download estimates with bytes delivered by S3."""
    from services.media_service.tasks.vault_bandwidth import (
        reconcile_vault_bandwidth,
    )

    return await reconcile_vault_bandwidth()


async def task_sync_session_vaults(ctx: dict):
    """Provision missing private vaults for recent and upcoming sessions."""
    from services.media_service.tasks.vault_lifecycle import sync_session_vaults

    return await sync_session_vaults()


# ── Worker configuration ──


class WorkerSettings:
    """ARQ worker settings for media processing."""

    redis_settings = get_redis_settings()
    queue_name = "arq:media"

    # Video transcoding is CPU-intensive — limit concurrent jobs
    max_jobs = 2

    # Long timeout for large video transcoding (15 minutes)
    job_timeout = 21600

    # Register task functions
    functions = [
        task_process_video,
        task_apply_audio,
        task_build_vault_export,
        task_build_vault_preview,
        task_cleanup_vault_exports,
        task_reconcile_vault_bandwidth,
        task_sync_session_vaults,
    ]

    cron_jobs = [
        cron(task_cleanup_vault_exports, hour={2}, minute=30),
        cron(task_reconcile_vault_bandwidth, minute={0, 15, 30, 45}),
        cron(task_sync_session_vaults, minute=5),
    ]
