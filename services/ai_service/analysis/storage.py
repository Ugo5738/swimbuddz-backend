"""Stroke Lab storage adapter.

Backend is configurable via ``STORAGE_BACKEND`` (``s3`` | ``supabase``) so Stroke Lab
sits on the SAME storage as the rest of the app (prod is S3) instead of its own
Supabase buckets. Two logical buckets:

  * ``strokelab-uploads``   — raw user/guest uploads. Private. Written by the API
                              POST endpoint, read by the ARQ worker.
  * ``strokelab-annotated`` — annotated mp4s + coach evidence frames (jpeg). Private.
                              Read by the API GET endpoint via a short-lived URL.

On ``s3`` these two become key prefixes inside one private S3 bucket
(``STROKELAB_S3_BUCKET`` or ``AWS_S3_BUCKET_PRIVATE``); on ``supabase`` they're
Supabase Storage buckets. Either way access is via short-lived signed/presigned
URLs (never truly public) so a clip can be revoked later. Bucket creation is
operator-side; the helpers assume the target bucket exists.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from libs.common.config import get_settings
from libs.common.logging import get_logger
from libs.common.supabase import get_supabase_admin_client

logger = get_logger(__name__)


UPLOADS_BUCKET = os.environ.get("STROKELAB_UPLOADS_BUCKET", "strokelab-uploads")
ANNOTATED_BUCKET = os.environ.get("STROKELAB_ANNOTATED_BUCKET", "strokelab-annotated")


# ── Storage backend — mirrors the rest of the app (STORAGE_BACKEND = s3 | supabase).
# On s3, Stroke Lab joins the app on AWS S3 instead of its own Supabase buckets; the
# logical bucket name (strokelab-uploads / strokelab-annotated) becomes the S3 key
# prefix inside one private bucket. (S3 has no per-bucket MIME allow-list, so the jpeg
# evidence frames that Supabase's video-only bucket rejected just work.)
def _use_s3() -> bool:
    return get_settings().STORAGE_BACKEND == "s3"


def _s3_client():
    import boto3  # lazy: only the s3 path needs it

    s = get_settings()
    return boto3.client(
        "s3",
        aws_access_key_id=s.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=s.AWS_SECRET_ACCESS_KEY,
        region_name=s.AWS_REGION,
    )


def _s3_bucket() -> str:
    s = get_settings()
    return s.STROKELAB_S3_BUCKET or s.AWS_S3_BUCKET_PRIVATE


def _s3_key(bucket: str, key: str) -> str:
    return f"{bucket}/{key}"  # logical bucket → key prefix in the single private bucket


# Default signed-URL lifetime for playback. One hour is plenty for a
# polling client + a video player to fetch the asset; rotated naturally
# as the player re-fetches the GET response.
DEFAULT_SIGNED_URL_TTL_SECONDS = 3600


def make_object_key(member_auth_id: uuid.UUID, job_id: uuid.UUID, suffix: str) -> str:
    """Storage key layout: ``{member_auth_id}/{job_id}.{suffix}``.

    Keying by member prefix gives us per-user delete + per-user usage
    queries for free (Supabase storage supports prefix listing).
    """
    return f"{member_auth_id}/{job_id}.{suffix}"


def make_guest_object_key(guest_token: str, job_id: uuid.UUID, suffix: str) -> str:
    """Storage key layout for PUBLIC/guest jobs: ``guest/{guest_token}/{job_id}.{suffix}``.

    Guests have no member id, so namespace under the unguessable per-job token
    (32 random bytes). The distinct ``guest/`` prefix keeps guest objects
    isolated from member uploads (``{member_auth_id}/...``).
    """
    return f"guest/{guest_token}/{job_id}.{suffix}"


# ── Sync helpers (thin wrappers over supabase-py's storage API) ────


def _upload_sync(bucket: str, key: str, data: bytes, content_type: str) -> None:
    if _use_s3():
        _s3_client().put_object(
            Bucket=_s3_bucket(),
            Key=_s3_key(bucket, key),
            Body=data,
            ContentType=content_type,
        )
        return
    client = get_supabase_admin_client()
    client.storage.from_(bucket).upload(
        key,
        data,
        file_options={"content-type": content_type, "upsert": "true"},
    )


def _download_sync(bucket: str, key: str) -> bytes:
    if _use_s3():
        resp = _s3_client().get_object(Bucket=_s3_bucket(), Key=_s3_key(bucket, key))
        return resp["Body"].read()
    client = get_supabase_admin_client()
    return client.storage.from_(bucket).download(key)


def _signed_url_sync(bucket: str, key: str, expires_in: int) -> str:
    if _use_s3():
        return _s3_client().generate_presigned_url(
            "get_object",
            Params={"Bucket": _s3_bucket(), "Key": _s3_key(bucket, key)},
            ExpiresIn=expires_in,
        )
    client = get_supabase_admin_client()
    res = client.storage.from_(bucket).create_signed_url(key, expires_in)
    # supabase-py returns {"signedURL": "..."} on success.
    url = res.get("signedURL") or res.get("signed_url") or ""
    if not url:
        raise RuntimeError(f"Supabase returned no signed URL for {bucket}/{key}: {res}")
    return url


def _delete_sync(bucket: str, key: str) -> None:
    if _use_s3():
        _s3_client().delete_object(Bucket=_s3_bucket(), Key=_s3_key(bucket, key))
        return
    client = get_supabase_admin_client()
    client.storage.from_(bucket).remove([key])


# ── Async-facing helpers (offload sync calls to a thread) ─────────


async def upload_user_video(
    member_auth_id: uuid.UUID,
    job_id: uuid.UUID,
    data: bytes,
    content_type: str = "video/mp4",
    suffix: str = "mp4",
) -> str:
    """Upload a user video. Returns the storage path."""
    key = make_object_key(member_auth_id, job_id, suffix)
    await asyncio.to_thread(_upload_sync, UPLOADS_BUCKET, key, data, content_type)
    return key


async def upload_guest_video(
    guest_token: str,
    job_id: uuid.UUID,
    data: bytes,
    content_type: str = "video/mp4",
    suffix: str = "mp4",
) -> str:
    """Upload a PUBLIC/guest video. Returns the storage path."""
    key = make_guest_object_key(guest_token, job_id, suffix)
    await asyncio.to_thread(_upload_sync, UPLOADS_BUCKET, key, data, content_type)
    return key


async def upload_annotated_video(
    member_auth_id: uuid.UUID,
    job_id: uuid.UUID,
    local_path: Path,
    content_type: str = "video/mp4",
    suffix: str = "mp4",
) -> str:
    """Upload an annotated mp4 from the worker's local filesystem."""
    key = make_object_key(member_auth_id, job_id, suffix)
    data = local_path.read_bytes()
    await asyncio.to_thread(_upload_sync, ANNOTATED_BUCKET, key, data, content_type)
    return key


async def upload_guest_annotated_video(
    guest_token: str,
    job_id: uuid.UUID,
    local_path: Path,
    content_type: str = "video/mp4",
    suffix: str = "mp4",
) -> str:
    """Upload a PUBLIC/guest annotated mp4 from the worker's local filesystem.

    Keys under ``guest/{guest_token}/...`` (guests have no member id), mirroring
    the original upload's prefix in the annotated bucket.
    """
    key = make_guest_object_key(guest_token, job_id, suffix)
    data = local_path.read_bytes()
    await asyncio.to_thread(_upload_sync, ANNOTATED_BUCKET, key, data, content_type)
    return key


@contextmanager
def temp_file_from_storage(bucket: str, key: str):
    """Sync context manager: download a stored object to a NamedTemporaryFile
    and yield its Path. The file is deleted on exit.

    Used inside the ARQ task (which is async) by wrapping in
    ``asyncio.to_thread`` — keeping a single sync impl avoids juggling
    async fds in cv2/MediaPipe code paths.
    """
    data = _download_sync(bucket, key)
    suffix = Path(key).suffix or ".bin"
    fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        yield Path(tmp_path)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


async def signed_url_for_upload(
    key: str, expires_in: int = DEFAULT_SIGNED_URL_TTL_SECONDS
) -> str:
    """Signed URL for the user's *original* uploaded clip."""
    return await asyncio.to_thread(_signed_url_sync, UPLOADS_BUCKET, key, expires_in)


async def signed_url_for_annotated(
    key: str, expires_in: int = DEFAULT_SIGNED_URL_TTL_SECONDS
) -> str:
    """Signed URL for the annotated mp4."""
    return await asyncio.to_thread(_signed_url_sync, ANNOTATED_BUCKET, key, expires_in)


# ── Coach evidence frames (reuse the annotated bucket; no new per-env bucket) ──


def make_evidence_key(
    prefix: str, job_id: uuid.UUID, label: str, subdir: str = "evidence"
) -> str:
    """Coach-image key ``{prefix}/{job_id}/{subdir}/{label}.jpg`` in the annotated
    bucket. ``prefix`` is ``{member_auth_id}`` or ``guest/{guest_token}``; ``label``
    (e.g. ``holistic_coach:3``) is sanitised; ``subdir`` is ``evidence`` or ``share``."""
    safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in label)
    return f"{prefix}/{job_id}/{subdir}/{safe}.jpg"


async def upload_evidence_frames(
    prefix: str, job_id: uuid.UUID, frames: dict[str, bytes], subdir: str = "evidence"
) -> dict[str, str]:
    """Upload coach images (label → jpeg bytes). Returns label → key. ``subdir``
    separates evidence frames from share cards."""
    keys: dict[str, str] = {}
    for label, data in frames.items():
        key = make_evidence_key(prefix, job_id, label, subdir)
        await asyncio.to_thread(_upload_sync, ANNOTATED_BUCKET, key, data, "image/jpeg")
        keys[label] = key
    return keys


async def signed_url_for_evidence(
    key: str, expires_in: int = DEFAULT_SIGNED_URL_TTL_SECONDS
) -> str:
    """Signed URL for a coach evidence frame (lives in the annotated bucket)."""
    return await asyncio.to_thread(_signed_url_sync, ANNOTATED_BUCKET, key, expires_in)


async def delete_job_assets(
    uploaded_key: Optional[str],
    annotated_key: Optional[str],
    evidence_keys: Optional[list[str]] = None,
) -> None:
    """Remove storage objects for a job. Best-effort — DELETE endpoint
    swallows storage failures so the DB row can still be removed. Includes coach
    evidence frames so erasure/retention sweeps don't leave orphaned images."""
    if uploaded_key:
        try:
            await asyncio.to_thread(_delete_sync, UPLOADS_BUCKET, uploaded_key)
        except Exception as exc:
            logger.warning("Could not delete upload %s: %s", uploaded_key, exc)
    if annotated_key:
        try:
            await asyncio.to_thread(_delete_sync, ANNOTATED_BUCKET, annotated_key)
        except Exception as exc:
            logger.warning("Could not delete annotated %s: %s", annotated_key, exc)
    for key in evidence_keys or []:
        try:
            await asyncio.to_thread(_delete_sync, ANNOTATED_BUCKET, key)
        except Exception as exc:
            logger.warning("Could not delete evidence %s: %s", key, exc)
