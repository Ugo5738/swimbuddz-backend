"""Backfill duration metadata without transcoding or replacing originals.

Run in the media service environment; defaults to a count-only preview.
python -m scripts.ops.backfill_vault_video_durations --apply --limit 500
"""

import argparse
import asyncio

from sqlalchemy import select

from libs.db.config import AsyncSessionLocal
from services.media_service.models import MediaItem, MediaType
from services.media_service.services.storage import BucketType, storage_service
from services.media_service.tasks.vault_previews import _probe_duration


async def run(*, apply: bool = False, limit: int = 500, concurrency: int = 3) -> dict:
    async with AsyncSessionLocal() as db:
        rows = list(
            (
                await db.execute(
                    select(MediaItem.id, MediaItem.object_key)
                    .where(
                        MediaItem.vault_id.is_not(None),
                        MediaItem.soft_deleted_at.is_(None),
                        MediaItem.media_type == MediaType.VIDEO,
                        MediaItem.processing_status == "ready",
                        MediaItem.object_key.is_not(None),
                        MediaItem.metadata_info["duration_seconds"].astext.is_(None),
                    )
                    .order_by(MediaItem.created_at)
                    .limit(limit)
                )
            ).all()
        )
    result = {"candidates": len(rows), "updated": 0, "unavailable": 0}
    if not apply:
        return result
    semaphore = asyncio.Semaphore(concurrency)

    async def probe(item_id, object_key):
        async with semaphore:
            try:
                url = await storage_service.generate_presigned_url(
                    object_key, BucketType.PRIVATE, 900
                )
                duration = await asyncio.to_thread(_probe_duration, url)
                if duration is None:
                    result["unavailable"] += 1
                    return
                async with AsyncSessionLocal() as db:
                    item = await db.get(MediaItem, item_id, with_for_update=True)
                    if item and not (item.metadata_info or {}).get("duration_seconds"):
                        item.metadata_info = {
                            **(item.metadata_info or {}),
                            "duration_seconds": duration,
                        }
                        await db.commit()
                        result["updated"] += 1
            except Exception:
                result["unavailable"] += 1

    await asyncio.gather(*(probe(item_id, key) for item_id, key in rows))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--limit", type=int, default=500)
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit must be positive")
    print(asyncio.run(run(apply=args.apply, limit=args.limit)))
