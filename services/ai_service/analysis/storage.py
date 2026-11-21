"""Stroke Lab storage adapter.

AI owns only opaque Stroke Lab storage references. The actual object operations
upload, sign, verify, and delete through media_service, so Stroke Lab follows the
same service-isolation pattern as the rest of the backend.

Legacy rows stored bare keys such as ``guest/{token}/{job_id}.mp4``. Those keys
are still resolved as objects under the historical private S3 prefixes
``strokelab-uploads`` and ``strokelab-annotated`` through media_service.
"""

from __future__ import annotations

import asyncio
import subprocess
import uuid
from pathlib import Path
from typing import Optional

import httpx
from libs.common.logging import get_logger
from libs.common.service_client.media import (
    delete_media_object,
    sign_media_object,
    upload_media_object,
    verify_media_object,
)

logger = get_logger(__name__)


LEGACY_UPLOADS_PREFIX = "strokelab-uploads"
LEGACY_ANNOTATED_PREFIX = "strokelab-annotated"
MEDIA_STORAGE_PREFIX = "media:"
_MEDIA_CALLING_SERVICE = "ai_service"


# Default signed-URL lifetime for playback. One hour is plenty for a
# polling client + a video player to fetch the asset; rotated naturally
# as the player re-fetches the GET response.
DEFAULT_SIGNED_URL_TTL_SECONDS = 3600


def media_storage_path(object_key: str) -> str:
    """Store media-service object keys as opaque AI storage references."""
    return f"{MEDIA_STORAGE_PREFIX}{object_key}"


def is_media_storage_path(path: str | None) -> bool:
    return bool(path and path.startswith(MEDIA_STORAGE_PREFIX))


def media_object_key(path: str) -> str:
    if not is_media_storage_path(path):
        raise ValueError("Not a media-service storage path")
    return path[len(MEDIA_STORAGE_PREFIX) :]


def _legacy_object_key(key: str, legacy_prefix: str) -> str:
    return f"{legacy_prefix}/{key.lstrip('/')}"


def _object_key_for_storage_path(path: str, legacy_prefix: str) -> str:
    if is_media_storage_path(path):
        return media_object_key(path)
    return _legacy_object_key(path, legacy_prefix)


async def _signed_url_for_path(path: str, legacy_prefix: str, expires_in: int) -> str:
    return await sign_media_object(
        object_key=_object_key_for_storage_path(path, legacy_prefix),
        calling_service=_MEDIA_CALLING_SERVICE,
        expires_in=expires_in,
    )


async def _delete_storage_path(path: str, legacy_prefix: str) -> None:
    await delete_media_object(
        object_key=_object_key_for_storage_path(path, legacy_prefix),
        calling_service=_MEDIA_CALLING_SERVICE,
    )


# ── Async-facing media_service helpers ───────────────────────────


async def upload_user_video(
    member_auth_id: uuid.UUID,
    job_id: uuid.UUID,
    data: bytes,
    content_type: str = "video/mp4",
    suffix: str = "mp4",
) -> str:
    """Upload a user video through media_service. Returns the storage path."""
    resp = await upload_media_object(
        purpose="strokelab_original",
        filename=f"{job_id}.{suffix}",
        content_type=content_type,
        data=data,
        linked_id=f"member/{member_auth_id}/{job_id}",
        calling_service=_MEDIA_CALLING_SERVICE,
    )
    return media_storage_path(str(resp["object_key"]))


async def upload_guest_video(
    guest_token: str,
    job_id: uuid.UUID,
    data: bytes,
    content_type: str = "video/mp4",
    suffix: str = "mp4",
) -> str:
    """Upload a PUBLIC/guest video through media_service. Returns the path."""
    resp = await upload_media_object(
        purpose="strokelab_original",
        filename=f"{job_id}.{suffix}",
        content_type=content_type,
        data=data,
        linked_id=f"guest/{guest_token}/{job_id}",
        calling_service=_MEDIA_CALLING_SERVICE,
    )
    return media_storage_path(str(resp["object_key"]))


async def upload_annotated_video(
    member_auth_id: uuid.UUID,
    job_id: uuid.UUID,
    local_path: Path,
    content_type: str = "video/mp4",
    suffix: str = "mp4",
) -> str:
    """Upload an annotated mp4 through media_service."""
    resp = await upload_media_object(
        purpose="strokelab_annotated",
        filename=f"{job_id}.{suffix}",
        content_type=content_type,
        data=local_path.read_bytes(),
        linked_id=f"member/{member_auth_id}/{job_id}",
        calling_service=_MEDIA_CALLING_SERVICE,
    )
    return media_storage_path(str(resp["object_key"]))


async def upload_guest_annotated_video(
    guest_token: str,
    job_id: uuid.UUID,
    local_path: Path,
    content_type: str = "video/mp4",
    suffix: str = "mp4",
) -> str:
    """Upload a PUBLIC/guest annotated mp4 from the worker's local filesystem.

    The guest token is retained in ``linked_id`` so media_service namespaces the
    object under a guest-specific Stroke Lab prefix in the private bucket.
    """
    resp = await upload_media_object(
        purpose="strokelab_annotated",
        filename=f"{job_id}.{suffix}",
        content_type=content_type,
        data=local_path.read_bytes(),
        linked_id=f"guest/{guest_token}/{job_id}",
        calling_service=_MEDIA_CALLING_SERVICE,
    )
    return media_storage_path(str(resp["object_key"]))


def _transcode_remote_clip(signed_url: str, dest: Path, max_seconds: int) -> None:
    """Decode only the initial coaching window directly from signed S3.

    Avoid a 2 GiB download and a second copy on a small worker. The original
    high-quality iPhone video remains untouched in the private bucket.
    """
    try:
        subprocess.run(
            [
                "ffmpeg", "-nostdin", "-y", "-hide_banner", "-loglevel", "error",
                "-i", signed_url, "-t", str(max_seconds), "-map", "0:v:0",
                "-an", "-vf", "scale=-2:720",
                "-c:v", "libx264", "-threads", "1", "-preset", "ultrafast",
                "-crf", "28", "-movflags", "+faststart", str(dest),
            ],
            check=True,
            capture_output=True,
            timeout=360,
        )
        if not dest.exists() or not dest.stat().st_size:
            raise RuntimeError("Remote analysis window was empty")
        if dest.stat().st_size > 200 * 1024 * 1024:
            raise RuntimeError("Remote analysis window exceeds processing budget")
    except Exception:
        dest.unlink(missing_ok=True)
        # CalledProcessError embeds the full presigned S3 URL in its exception.
        # Suppress its context so the bearer URL cannot leak into worker logs.
        raise RuntimeError("Could not prepare remote analysis window") from None


async def download_storage_path(
    storage_path: str,
    dest_dir: Path,
    *,
    legacy_prefix: str = LEGACY_UPLOADS_PREFIX,
    public_window_seconds: int | None = None,
) -> Path:
    """Download an AI storage reference into ``dest_dir`` through media_service."""
    object_key = _object_key_for_storage_path(storage_path, legacy_prefix)
    signed_url = await _signed_url_for_path(
        storage_path, legacy_prefix, DEFAULT_SIGNED_URL_TTL_SECONDS
    )
    dest = dest_dir / (Path(object_key).name or "upload.bin")
    if public_window_seconds and is_media_storage_path(storage_path):
        # For very large original files, never download the entire video to
        # local disk. FFmpeg pulls the first two minutes over HTTPS and writes
        # a bounded 720/1280p working clip.
        meta = await verify_media_object(
            object_key=object_key, calling_service=_MEDIA_CALLING_SERVICE,
        )
        if int(meta.get("size_bytes") or 0) > 250 * 1024 * 1024:
            from services.ai_service.constants import PUBLIC_MAX_UPLOAD_BYTES
            if int(meta["size_bytes"]) > PUBLIC_MAX_UPLOAD_BYTES:
                raise ValueError("StrokeLab video exceeds upload limit")
            clip = dest_dir / f"{Path(object_key).stem}_window.mp4"
            await asyncio.to_thread(
                _transcode_remote_clip, signed_url, clip, public_window_seconds,
            )
            return clip
    # Stream into the temporary file: httpx.get() buffers the entire response
    # in RAM and could kill the 2 GiB worker on an accepted large upload.
    from services.ai_service.constants import PUBLIC_MAX_UPLOAD_BYTES

    downloaded = 0
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream("GET", signed_url) as resp:
                resp.raise_for_status()
                content_length = int(resp.headers.get("content-length") or 0)
                if content_length > PUBLIC_MAX_UPLOAD_BYTES:
                    raise ValueError("StrokeLab video exceeds worker size limit")
                with dest.open("wb") as output:
                    async for chunk in resp.aiter_bytes(chunk_size=1024 * 1024):
                        downloaded += len(chunk)
                        if downloaded > PUBLIC_MAX_UPLOAD_BYTES:
                            raise ValueError(
                                "StrokeLab video exceeds worker size limit"
                            )
                        output.write(chunk)
        if not downloaded:
            raise ValueError("StrokeLab video is empty")
        return dest
    except Exception:
        dest.unlink(missing_ok=True)
        raise


async def signed_url_for_upload(
    key: str, expires_in: int = DEFAULT_SIGNED_URL_TTL_SECONDS
) -> str:
    """Signed URL for the user's *original* uploaded clip."""
    return await _signed_url_for_path(key, LEGACY_UPLOADS_PREFIX, expires_in)


async def signed_url_for_annotated(
    key: str, expires_in: int = DEFAULT_SIGNED_URL_TTL_SECONDS
) -> str:
    """Signed URL for the annotated mp4."""
    return await _signed_url_for_path(key, LEGACY_ANNOTATED_PREFIX, expires_in)


# ── Coach evidence frames (reuse the annotated bucket; no new per-env bucket) ──


def make_evidence_key(
    prefix: str, job_id: uuid.UUID, label: str, subdir: str = "evidence"
) -> str:
    """Legacy-shaped coach-image name used as the media upload filename.

    ``prefix`` is ``{member_auth_id}`` or ``guest/{guest_token}``; ``label``
    (e.g. ``holistic_coach:3``) is sanitised; ``subdir`` is ``evidence`` or
    ``share``.
    """
    safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in label)
    return f"{prefix}/{job_id}/{subdir}/{safe}.jpg"


async def upload_evidence_frames(
    prefix: str, job_id: uuid.UUID, frames: dict[str, bytes], subdir: str = "evidence"
) -> dict[str, str]:
    """Upload coach images (label -> jpeg bytes). Returns label -> storage path."""
    keys: dict[str, str] = {}
    for label, data in frames.items():
        legacy_key = make_evidence_key(prefix, job_id, label, subdir)
        purpose = "strokelab_share" if subdir == "share" else "strokelab_evidence"
        resp = await upload_media_object(
            purpose=purpose,
            filename=f"{Path(legacy_key).name}",
            content_type="image/jpeg",
            data=data,
            linked_id=f"{prefix}/{job_id}/{subdir}",
            calling_service=_MEDIA_CALLING_SERVICE,
        )
        keys[label] = media_storage_path(str(resp["object_key"]))
    return keys


async def signed_url_for_evidence(
    key: str, expires_in: int = DEFAULT_SIGNED_URL_TTL_SECONDS
) -> str:
    """Signed URL for a coach evidence frame (lives in the annotated bucket)."""
    return await _signed_url_for_path(key, LEGACY_ANNOTATED_PREFIX, expires_in)


async def delete_job_assets(
    uploaded_key: Optional[str],
    annotated_key: Optional[str],
    evidence_keys: Optional[list[str]] = None,
) -> bool:
    """Remove storage objects for a job. Best-effort — DELETE endpoint
    swallows storage failures so the DB row can still be removed. Includes coach
    evidence frames so erasure/retention sweeps don't leave orphaned images."""
    complete = True
    if uploaded_key:
        try:
            await _delete_storage_path(uploaded_key, LEGACY_UPLOADS_PREFIX)
        except Exception as exc:
            complete = False
            logger.warning("Could not delete upload %s: %s", uploaded_key, exc)
    if annotated_key:
        try:
            await _delete_storage_path(annotated_key, LEGACY_ANNOTATED_PREFIX)
        except Exception as exc:
            complete = False
            logger.warning("Could not delete annotated %s: %s", annotated_key, exc)
    for key in evidence_keys or []:
        try:
            await _delete_storage_path(key, LEGACY_ANNOTATED_PREFIX)
        except Exception as exc:
            complete = False
            logger.warning("Could not delete evidence %s: %s", key, exc)

    return complete
