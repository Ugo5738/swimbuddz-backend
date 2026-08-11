"""Lightweight review derivatives for vault media.

Original objects are never modified or replaced. Small thumbnails are built
automatically; playable video proxies remain curator-requested.
"""

from __future__ import annotations

import subprocess
import tempfile
import uuid
from pathlib import Path

from PIL import Image, ImageOps

from libs.common.logging import get_logger
from libs.db.config import AsyncSessionLocal
from services.media_service.models import MediaItem, MediaType
from services.media_service.services.storage import storage_service

logger = get_logger(__name__)


def _run_video_proxy(input_path: str, proxy_path: str) -> None:
    proxy = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            input_path,
            "-c:v",
            "libx264",
            "-crf",
            "25",
            "-preset",
            "fast",
            "-vf",
            "scale='min(1280,iw)':-2",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            proxy_path,
        ],
        capture_output=True,
        text=True,
        timeout=3600,
    )
    if proxy.returncode != 0:
        raise RuntimeError(f"ffmpeg proxy failed: {proxy.stderr[-800:]}")


def _run_video_thumbnail(input_path: str, thumbnail_path: str) -> None:
    thumbnail = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-ss",
            "00:00:01",
            "-i",
            input_path,
            "-frames:v",
            "1",
            "-vf",
            "scale='min(960,iw)':-2",
            thumbnail_path,
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    if thumbnail.returncode != 0:
        logger.warning(
            "Could not generate video thumbnail: %s", thumbnail.stderr[-400:]
        )


async def build_vault_preview(
    media_item_id: str, generate_video_proxy: bool = True
) -> dict:
    item_uuid = uuid.UUID(media_item_id)
    async with AsyncSessionLocal() as db:
        item = await db.get(MediaItem, item_uuid)
        if not item or not item.vault_id or not item.object_key:
            return {"status": "missing"}
        is_video = item.media_type == MediaType.VIDEO
        derivative_exists = (
            item.proxy_object_key
            if is_video and generate_video_proxy
            else item.thumbnail_object_key
        )
        if derivative_exists:
            return {"status": "ready"}
        metadata = dict(item.metadata_info or {})
        status_key = (
            "proxy_status" if is_video and generate_video_proxy else "thumbnail_status"
        )
        metadata[status_key] = "processing"
        item.metadata_info = metadata
        await db.commit()
        object_key = item.object_key
        media_type = item.media_type
        vault_id = item.vault_id
        original_filename = item.original_filename
        existing_proxy_key = item.proxy_object_key
        existing_thumbnail_key = item.thumbnail_object_key

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            suffix = Path(original_filename or "").suffix or ".bin"
            input_path = str(Path(temp_dir) / f"original{suffix}")
            storage_service.s3_client.download_file(
                storage_service.bucket_private,
                object_key,
                input_path,
            )
            derivative_prefix = f"vault-derivatives/{vault_id}/{item_uuid}"
            proxy_key = existing_proxy_key
            thumbnail_key = existing_thumbnail_key

            if media_type == MediaType.VIDEO:
                if not thumbnail_key:
                    thumbnail_path = str(Path(temp_dir) / "thumbnail.jpg")
                    _run_video_thumbnail(input_path, thumbnail_path)
                    thumbnail_key = f"{derivative_prefix}/thumbnail.jpg"
                    with open(thumbnail_path, "rb") as thumbnail_file:
                        await storage_service.upload_private_fileobj(
                            file_key=thumbnail_key,
                            fileobj=thumbnail_file,
                            content_type="image/jpeg",
                        )
                if generate_video_proxy and not proxy_key:
                    proxy_path = str(Path(temp_dir) / "review-proxy.mp4")
                    _run_video_proxy(input_path, proxy_path)
                    proxy_key = f"{derivative_prefix}/review-proxy.mp4"
                    with open(proxy_path, "rb") as proxy_file:
                        await storage_service.upload_private_fileobj(
                            file_key=proxy_key,
                            fileobj=proxy_file,
                            content_type="video/mp4",
                        )
            elif media_type == MediaType.IMAGE:
                thumbnail_path = str(Path(temp_dir) / "thumbnail.jpg")
                try:
                    with Image.open(input_path) as image:
                        image = ImageOps.exif_transpose(image)
                        image.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
                        if image.mode not in ("RGB", "L"):
                            image = image.convert("RGB")
                        image.save(thumbnail_path, "JPEG", quality=88, optimize=True)
                except Exception:
                    # Debian ffmpeg commonly has libheif even when Pillow does
                    # not, so iPhone HEIC originals still get a review image.
                    converted = subprocess.run(
                        [
                            "ffmpeg",
                            "-y",
                            "-i",
                            input_path,
                            "-vf",
                            "scale='min(1600,iw)':-2",
                            "-frames:v",
                            "1",
                            thumbnail_path,
                        ],
                        capture_output=True,
                        text=True,
                        timeout=300,
                    )
                    if converted.returncode != 0:
                        raise RuntimeError(
                            f"image preview failed: {converted.stderr[-800:]}"
                        )
                thumbnail_key = f"{derivative_prefix}/thumbnail.jpg"
                with open(thumbnail_path, "rb") as thumbnail_file:
                    await storage_service.upload_private_fileobj(
                        file_key=thumbnail_key,
                        fileobj=thumbnail_file,
                        content_type="image/jpeg",
                    )

        async with AsyncSessionLocal() as db:
            item = await db.get(MediaItem, item_uuid)
            if not item:
                return {"status": "missing-after-build"}
            item.proxy_object_key = proxy_key
            item.thumbnail_object_key = thumbnail_key
            metadata = dict(item.metadata_info or {})
            metadata[status_key] = "ready"
            if thumbnail_key:
                metadata["thumbnail_status"] = "ready"
            metadata["original_preserved"] = True
            item.metadata_info = metadata
            await db.commit()
        return {"status": "ready"}
    except Exception as exc:
        logger.exception("Vault preview generation failed for %s", media_item_id)
        async with AsyncSessionLocal() as db:
            item = await db.get(MediaItem, item_uuid)
            if item:
                metadata = dict(item.metadata_info or {})
                metadata[status_key] = "failed"
                metadata[f"{status_key}_error"] = str(exc)[:1000]
                item.metadata_info = metadata
                await db.commit()
        raise
