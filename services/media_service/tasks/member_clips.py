"""Short, private member clip derivatives rendered from playback proxies."""
from __future__ import annotations

import asyncio
import subprocess
import tempfile
import uuid
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select

from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from libs.db.config import AsyncSessionLocal
from services.media_service.models import MediaItem, MediaVaultClip
from services.media_service.services.storage import BucketType, storage_service

logger = get_logger(__name__)


async def build_member_vault_clip(clip_id: str) -> dict:
    uid = uuid.UUID(clip_id)
    async with AsyncSessionLocal() as db:
        clip = await db.get(MediaVaultClip, uid)
        if not clip or clip.status != "pending":
            return {"status": "not_pending"}
        item = await db.get(MediaItem, clip.media_item_id)
        if not item or not item.proxy_object_key or not item.allow_member_clips:
            clip.status = "failed"
            clip.error_message = "Source preview is no longer accessible"
            await db.commit()
            return {"status": "failed"}
        clip.status = "processing"
        await db.commit()
        source_key = item.proxy_object_key
        vault_id = item.vault_id
        start = clip.start_ms / 1000
        duration = (clip.end_ms - clip.start_ms) / 1000
    try:
        with tempfile.TemporaryDirectory() as folder:
            source = str(Path(folder) / "preview.mp4")
            target = str(Path(folder) / "clip.mp4")
            await asyncio.to_thread(
                storage_service.s3_client.download_file,
                storage_service.bucket_private, source_key, source
            )
            def render():
                return subprocess.run(
                    ["ffmpeg", "-y", "-ss", str(start), "-i", source,
                     "-t", str(duration), "-c:v", "libx264", "-preset", "veryfast",
                     "-crf", "26", "-c:a", "aac", "-b:a", "96k", "-movflags",
                     "+faststart", target],
                    capture_output=True, text=True, timeout=600,
                )
            result = await asyncio.to_thread(render)
            if result.returncode:
                raise RuntimeError(result.stderr[-500:])
            object_key = f"vault-derivatives/{vault_id}/{uid}/member-clip.mp4"
            with open(target, "rb") as stream:
                await storage_service.upload_private_fileobj(
                    file_key=object_key, fileobj=stream, content_type="video/mp4"
                )
        async with AsyncSessionLocal() as db:
            clip = await db.get(MediaVaultClip, uid)
            if not clip:
                return {"status": "missing"}
            clip.status = "ready"
            clip.object_key = object_key
            await db.commit()
        return {"status": "ready"}
    except Exception as exc:
        logger.exception("Member video clip %s failed", uid)
        async with AsyncSessionLocal() as db:
            clip = await db.get(MediaVaultClip, uid)
            if clip:
                clip.status = "failed"
                clip.error_message = str(exc)[:500]
                await db.commit()
        return {"status": "failed"}


async def cleanup_expired_member_clips() -> dict:
    """Remove expired derivatives, leaving source recordings unchanged."""
    async with AsyncSessionLocal() as db:
        clips = list((await db.scalars(
            select(MediaVaultClip).where(
                MediaVaultClip.expires_at < utc_now(),
                MediaVaultClip.object_key.is_not(None),
            ).limit(100)
        )).all())
        count = 0
        for clip in clips:
            try:
                await storage_service.delete_file(clip.object_key, BucketType.PRIVATE)
                clip.object_key = None
                clip.status = "expired"
                count += 1
            except Exception:
                logger.exception("Could not clean up member clip %s", clip.id)
        await db.commit()
    return {"deleted": count}
