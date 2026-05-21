"""Admin-side media access for academy enrollment evidence.

Two endpoints:

* ``GET /media/admin/enrollments/{enrollment_id}/evidence`` — the
  evidence gallery for an enrollment. One tile per StudentProgress
  claim that has a linked media item.
* ``GET /media/admin/items/{media_id}/download`` — short-lived
  presigned URL the browser uses to save the asset to disk.

Both endpoints write to ``media_audit_logs``. List logs one row per
surfaced item (so a future "all administrative access of this asset"
query is a single ``WHERE entity_id =`` predicate); download logs a
single row at URL issuance time.

See ``docs/design/ACADEMY_ADMIN_CONTROLS_DESIGN.md`` §4.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request, status
from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from libs.common.service_client import list_enrollment_progress
from libs.db.session import get_async_db
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.media_service.models import MediaItem
from services.media_service.routers._helpers import _maybe_presign_url
from services.media_service.schemas import (
    AdminEvidenceItemResponse,
    AdminEvidenceListResponse,
    MediaDownloadResponse,
)
from services.media_service.services.audit import (
    ACTION_DOWNLOAD,
    ACTION_LIST,
    write_audit,
    write_audit_bulk,
)
from services.media_service.services.storage import storage_service

logger = get_logger(__name__)

router = APIRouter(prefix="/media/admin", tags=["media-admin"])

# Short-lived download URL — 60 seconds is enough for an admin's browser
# to start the GET to S3 and contains accidental sharing. See design §9.3.
DOWNLOAD_URL_TTL_SECONDS = 60


@router.get(
    "/enrollments/{enrollment_id}/evidence",
    response_model=AdminEvidenceListResponse,
)
async def list_enrollment_evidence(
    enrollment_id: uuid.UUID,
    request: Request,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
) -> AdminEvidenceListResponse:
    """Return every milestone-evidence media item for an enrollment.

    Joins academy's StudentProgress (fetched over HTTP — services
    cannot share a DB) with the local ``media_items`` rows. Items
    that the academy reports but media can't locate are dropped from
    the response with a logger.warning; this means a deleted media
    row never surfaces an empty card but the operator sees the
    discrepancy in logs.
    """
    progress_rows = await list_enrollment_progress(
        str(enrollment_id), calling_service="media"
    )

    # Collect distinct evidence_media_ids (NULL/missing rows are simply
    # absent from the gallery — they have no media to display).
    progress_by_media: dict[uuid.UUID, dict] = {}
    for row in progress_rows:
        raw = row.get("evidence_media_id")
        if not raw:
            continue
        try:
            media_uuid = uuid.UUID(raw)
        except (TypeError, ValueError):
            logger.warning(
                "Skipping malformed evidence_media_id %r on progress %s",
                raw,
                row.get("id"),
            )
            continue
        # If the same media is referenced by multiple progress rows
        # (rare, but possible historically), keep the most recently
        # achieved one for display context.
        existing = progress_by_media.get(media_uuid)
        existing_at = existing and existing.get("achieved_at")
        candidate_at = row.get("achieved_at")
        if existing is None or (
            candidate_at and (not existing_at or candidate_at > existing_at)
        ):
            progress_by_media[media_uuid] = row

    if not progress_by_media:
        return AdminEvidenceListResponse(
            items=[],
            enrollment_id=enrollment_id,
            total=0,
        )

    media_ids = list(progress_by_media.keys())
    media_rows_q = await db.execute(
        select(MediaItem).where(MediaItem.id.in_(media_ids))
    )
    media_rows = {m.id: m for m in media_rows_q.scalars().all()}

    items: list[AdminEvidenceItemResponse] = []
    for media_uuid, progress in progress_by_media.items():
        media = media_rows.get(media_uuid)
        if media is None:
            # Academy thinks this media exists but media-service has no
            # row. Most likely a deletion in the past that didn't cascade
            # — log for the operator, skip for the UI.
            logger.warning(
                "Academy progress %s references unknown media_item %s — skipping",
                progress.get("id"),
                media_uuid,
            )
            continue
        items.append(
            AdminEvidenceItemResponse(
                media_id=media.id,
                media_type=str(
                    media.media_type.value
                    if hasattr(media.media_type, "value")
                    else media.media_type
                ),
                file_url=_maybe_presign_url(media.file_url),
                thumbnail_url=_maybe_presign_url(media.thumbnail_url),
                is_processed=bool(media.is_processed),
                media_created_at=media.created_at,
                enrollment_id=enrollment_id,
                milestone_id=uuid.UUID(progress["milestone_id"]),
                progress_id=uuid.UUID(progress["id"]),
                progress_status=str(progress.get("status") or ""),
                student_notes=progress.get("student_notes"),
                claim_achieved_at=(
                    progress.get("achieved_at") and _parse_iso(progress["achieved_at"])
                ),
            )
        )

    # Sort newest-claim-first so the most recently surfaced evidence
    # leads the gallery.
    items.sort(
        key=lambda r: (r.claim_achieved_at or r.media_created_at),
        reverse=True,
    )

    # Audit — one row per surfaced item.
    await write_audit_bulk(
        db,
        action=ACTION_LIST,
        actor=current_user,
        entity_ids=[i.media_id for i in items],
        request=request,
        reason=f"enrollment_evidence_gallery:{enrollment_id}",
    )
    await db.commit()

    return AdminEvidenceListResponse(
        items=items,
        enrollment_id=enrollment_id,
        total=len(items),
    )


@router.get(
    "/items/{media_id}/download",
    response_model=MediaDownloadResponse,
)
async def get_media_download_url(
    media_id: uuid.UUID,
    request: Request,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
) -> MediaDownloadResponse:
    """Issue a 60-second presigned URL for an admin to download a media item.

    The bytes do not stream through this service — see design §9.3.
    The audit row is written when the URL is issued (records intent),
    not when the download completes (which we can't observe).
    """
    media = (
        await db.execute(select(MediaItem).where(MediaItem.id == media_id))
    ).scalar_one_or_none()
    if media is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Media item not found",
        )

    download_url = _presign_for_download(media.file_url)
    if not download_url:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Cannot produce a download URL for this media item",
        )

    await write_audit(
        db,
        action=ACTION_DOWNLOAD,
        actor=current_user,
        entity_id=media.id,
        request=request,
        reason=None,
    )
    await db.commit()

    return MediaDownloadResponse(
        download_url=download_url,
        expires_at=utc_now() + timedelta(seconds=DOWNLOAD_URL_TTL_SECONDS),
    )


# ── Internal helpers ──────────────────────────────────────────────


def _presign_for_download(file_url: Optional[str]) -> Optional[str]:
    """Return a short-lived presigned URL for the download endpoint.

    Unlike ``_maybe_presign_url`` (which uses a 1h TTL appropriate
    for in-page playback), the download path uses a deliberately
    short TTL: the URL only needs to live long enough for the
    browser to start its GET on S3.
    """
    if not file_url:
        return None
    if storage_service.backend != "s3":
        # Local / Supabase backend: the file_url is already
        # browser-fetchable.
        return file_url
    private_bucket = getattr(storage_service, "bucket_private", "") or ""
    if private_bucket and private_bucket in file_url:
        key = urlparse(file_url).path.lstrip("/")
        if not key:
            return None
        try:
            # ``generate_presigned_url`` is async on the storage
            # service, but underlying boto3 is sync. Calling the
            # sync s3 client directly keeps this helper sync and
            # matches the pattern in ``_helpers._maybe_presign_url``.
            return storage_service.s3_client.generate_presigned_url(
                "get_object",
                Params={"Bucket": private_bucket, "Key": key},
                ExpiresIn=DOWNLOAD_URL_TTL_SECONDS,
            )
        except Exception as e:
            logger.warning("Could not presign for download (%s): %s", file_url[:80], e)
            return None
    # Public / CloudFront URL — return as-is.
    return file_url


def _parse_iso(value):
    """Tolerant ISO-8601 parser for academy's HTTP response payloads.

    Academy returns timestamps as ISO-8601 strings (Pydantic v2 default).
    We accept either a datetime (already coerced upstream) or a string.
    """
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value
    from datetime import datetime

    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
