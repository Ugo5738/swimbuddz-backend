"""Bounded, resumable background backfill for existing private-vault videos.

One historical video at a time may be queued/running. New uploads and manually
requested previews use the normal media queue without waiting for a full backfill.
The source video and publication/consent settings are never modified.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import Integer, and_, cast, func, or_, select

from libs.common.logging import get_logger
from libs.db.config import AsyncSessionLocal
from services.media_service.models import MediaItem, MediaType

logger = get_logger(__name__)

# Longer than the media worker's six-hour job timeout. An orphaned queue entry
# becomes eligible for recovery instead of keeping backfill stuck indefinitely.
STALE_AFTER = timedelta(hours=8)
RETRY_AFTER = timedelta(minutes=15)
MAX_ATTEMPTS = 3
MAX_IN_FLIGHT = 1


def preview_needs_backfill(metadata: dict, now: datetime) -> bool:
    """Decide if a missing proxy can be safely (re)queued."""
    status = metadata.get("proxy_status")
    attempts = int(metadata.get("auto_preview_attempts") or 0)
    if attempts >= MAX_ATTEMPTS:
        return False
    queued = metadata.get("auto_preview_queued_at")
    queued_at = None
    if queued:
        try:
            queued_at = datetime.fromisoformat(queued).astimezone(timezone.utc)
        except (ValueError, TypeError):
            pass
    if status in {"pending", "processing"}:
        # Never duplicate a curator-requested job with no backfill marker.
        return queued_at is not None and queued_at < now - STALE_AFTER
    if status == "failed" and queued_at is not None:
        return queued_at < now - RETRY_AFTER
    return True


async def enqueue_missing_vault_video_preview(redis_pool) -> dict:
    """Queue at most one eligible video per cron tick, across all vaults."""
    now = datetime.now(timezone.utc)
    stale = (now - STALE_AFTER).isoformat()
    retry = (now - RETRY_AFTER).isoformat()
    status = MediaItem.metadata_info["proxy_status"].astext
    queued_at = MediaItem.metadata_info["auto_preview_queued_at"].astext
    attempts = func.coalesce(
        cast(MediaItem.metadata_info["auto_preview_attempts"].astext, Integer), 0
    )
    scope = (
        MediaItem.vault_id.is_not(None),
        MediaItem.object_key.is_not(None),
        MediaItem.media_type == MediaType.VIDEO,
        MediaItem.processing_status == "ready",
        MediaItem.soft_deleted_at.is_(None),
        MediaItem.proxy_object_key.is_(None),
    )
    async with AsyncSessionLocal() as db:
        in_flight = await db.scalar(
            select(func.count(MediaItem.id)).where(
                *scope,
                status.in_(["pending", "processing"]),
                queued_at.is_not(None),
                queued_at >= stale,
            )
        )
        if int(in_flight or 0) >= MAX_IN_FLIGHT:
            return {"status": "busy"}

        # SQL filtering prevents permanently failed videos from blocking
        # other uploads as the library grows beyond its current size.
        item = await db.scalar(
            select(MediaItem)
            .where(
                *scope,
                attempts < MAX_ATTEMPTS,
                or_(
                    status.is_(None),
                    and_(status == "failed", or_(queued_at.is_(None), queued_at < retry)),
                    and_(
                        status.in_(["pending", "processing"]),
                        queued_at.is_not(None),
                        queued_at < stale,
                    ),
                ),
            )
            .order_by(MediaItem.created_at.asc())
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if not item:
            return {"status": "complete"}

        metadata = dict(item.metadata_info or {})
        if not preview_needs_backfill(metadata, now):
            return {"status": "skipped"}
        metadata["auto_preview_attempts"] = int(metadata.get("auto_preview_attempts") or 0) + 1
        metadata["auto_preview_queued_at"] = now.isoformat()
        metadata["proxy_status"] = "pending"
        item.metadata_info = metadata
        item_id = str(item.id)
        await db.commit()

    try:
        await redis_pool.enqueue_job(
            "task_build_vault_preview",
            item_id,
            True,
            False,
            _queue_name="arq:media",
        )
    except Exception:
        logger.exception("Could not enqueue vault backfill preview %s", item_id)
        # Don't burn a retry when Redis is down. The next cron tick can recover.
        async with AsyncSessionLocal() as db:
            item = await db.get(MediaItem, item.id)
            if item and not item.proxy_object_key:
                metadata = dict(item.metadata_info or {})
                if (
                    metadata.get("proxy_status") == "pending"
                    and metadata.get("auto_preview_queued_at") == now.isoformat()
                ):
                    metadata["proxy_status"] = "failed"
                    metadata["auto_preview_attempts"] = max(
                        0, int(metadata.get("auto_preview_attempts") or 0) - 1
                    )
                    metadata.pop("auto_preview_queued_at", None)
                    item.metadata_info = metadata
                    await db.commit()
        return {"status": "queue_error"}
    return {"status": "queued", "item_id": item_id}
