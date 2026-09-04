"""Private, session/date-specific media vault API."""

from __future__ import annotations

import hashlib
import math
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import PurePath
from typing import Optional

from arq import create_pool
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import String, case, cast, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import get_current_user, require_admin
from libs.auth.models import AuthUser
from libs.common.arq_config import get_redis_settings
from libs.common.config import get_settings
from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from libs.common.service_client import (
    dispatch_notification,
)
from libs.db.session import get_async_db
from services.media_service.models import (
    Album,
    AlbumItem,
    AlbumType,
    MediaAccessLogEvent,
    MediaAccessLogObject,
    MediaItem,
    MediaTakedownRequest,
    MediaTransferLog,
    MediaType,
    MediaUploadBatch,
    MediaVault,
    MediaVaultExport,
    MediaVaultGrant,
    MediaVaultGuestLink,
)
from services.media_service.schemas import (
    BandwidthSummary,
    BulkReviewRequest,
    DownloadAuthorizationResponse,
    ExportCreate,
    ExportResponse,
    GuestLinkCreate,
    GuestLinkResponse,
    GuestVaultResponse,
    MultipartCompleteRequest,
    MultipartInitiateRequest,
    MultipartInitiateResponse,
    MultipartSignPartsRequest,
    MultipartSignPartsResponse,
    PublishRequest,
    ReviewUpdate,
    TakedownCreate,
    TakedownResolve,
    TakedownResponse,
    TransferCompletionRequest,
    UploadBatchCreate,
    UploadBatchResponse,
    VaultCreate,
    VaultGrantCreate,
    VaultGrantResponse,
    VaultListResponse,
    VaultMediaListResponse,
    VaultMediaDeleteRequest,
    VaultMediaDeleteResponse,
    VaultMediaResponse,
    VaultResponse,
    VaultUpdate,
)
from services.media_service.services.storage import (
    AWS_REGION,
    BucketType,
    recommended_multipart_part_size,
    storage_service,
)
from services.media_service.services.audit import write_audit
from services.media_service.services.vault_grants import (
    ensure_contributor_window,
    notify_vault_access,
    sync_volunteer_grants as sync_vault_volunteer_grants,
)
from services.media_service.services.vault_templates import (
    DEFAULT_MEDIA_VAULT_CHECKLIST,
    DEFAULT_MEDIA_VAULT_CONSENT_NOTICE,
    MEDIA_VAULT_UPLOAD_WINDOW_HOURS,
    default_media_coverage_settings,
)
from services.media_service.services.vault_access import (
    ROLE_RANK,
    VaultActor,
    effective_vault_role,
    get_vault_or_404,
    require_upload_window,
    require_vault_role,
    resolve_actor,
)

router = APIRouter(prefix="/media/vaults", tags=["media-vaults"])
settings = get_settings()
logger = get_logger(__name__)
_redis_pool = None
_FILENAME_SAFE = re.compile(r"[^A-Za-z0-9._()\- ]+")
_SUPPORTED_FALLBACK_EXTENSIONS = {
    ".heic",
    ".heif",
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".tif",
    ".tiff",
    ".mov",
    ".mp4",
    ".m4v",
    ".avi",
    ".mkv",
}


async def _get_redis_pool():
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = await create_pool(get_redis_settings())
    return _redis_pool


def _safe_filename(filename: str) -> str:
    basename = PurePath(filename.replace("\\", "/")).name.strip()
    cleaned = _FILENAME_SAFE.sub("_", basename)[:240]
    return cleaned or f"upload-{uuid.uuid4()}"


def _canonical_private_url(key: str) -> str:
    return (
        f"https://{storage_service.bucket_private}.s3.{AWS_REGION}."
        f"amazonaws.com/{key}"
    )


def _media_type_for(content_type: str, filename: str) -> MediaType:
    normalized = content_type.split(";", 1)[0].lower()
    if normalized.startswith("image/"):
        return MediaType.IMAGE
    if normalized.startswith("video/"):
        return MediaType.VIDEO
    extension = PurePath(filename.lower()).suffix
    if normalized == "application/octet-stream" and extension in {
        ".mov",
        ".mp4",
        ".m4v",
        ".avi",
        ".mkv",
    }:
        return MediaType.VIDEO
    if normalized == "application/octet-stream" and extension in (
        _SUPPORTED_FALLBACK_EXTENSIONS - {".mov", ".mp4", ".m4v", ".avi", ".mkv"}
    ):
        return MediaType.IMAGE
    raise HTTPException(
        status_code=415,
        detail="Only full-quality image and video originals are accepted",
    )


def _request_metadata(request: Request) -> tuple[Optional[str], Optional[str]]:
    forwarded = request.headers.get("x-forwarded-for")
    ip = forwarded.split(",", 1)[0].strip() if forwarded else None
    if not ip and request.client:
        ip = request.client.host
    return ip, request.headers.get("user-agent")


async def _vault_response(
    db: AsyncSession, vault: MediaVault, actor: VaultActor
) -> VaultResponse:
    counts = await db.execute(
        select(
            func.count(MediaItem.id),
            func.count(case((MediaItem.review_status == "unreviewed", MediaItem.id))),
        ).where(
            MediaItem.vault_id == vault.id,
            MediaItem.soft_deleted_at.is_(None),
            MediaItem.processing_status == "ready",
        )
    )
    item_count, pending_count = counts.one()
    role = await effective_vault_role(db, vault_id=vault.id, actor=actor)
    response = VaultResponse.model_validate(vault)
    return response.model_copy(
        update={
            "effective_role": role,
            "item_count": int(item_count or 0),
            "pending_review_count": int(pending_count or 0),
        }
    )


async def _media_response(item: MediaItem) -> VaultMediaResponse:
    """Attach short-lived URLs only for small, explicitly-built derivatives."""
    response = VaultMediaResponse.model_validate(item)
    ttl = settings.MEDIA_VAULT_SIGNED_URL_TTL_SECONDS
    preview_url = None
    thumbnail_url = None
    if item.proxy_object_key:
        preview_url = await storage_service.generate_presigned_url(
            item.proxy_object_key, BucketType.PRIVATE, ttl
        )
    if item.thumbnail_object_key:
        thumbnail_url = await storage_service.generate_presigned_url(
            item.thumbnail_object_key, BucketType.PRIVATE, ttl
        )
        if item.media_type == MediaType.IMAGE:
            preview_url = thumbnail_url
    metadata = dict(item.metadata_info or {})
    if item.processing_status != "ready":
        preview_status = "unavailable"
    elif preview_url:
        preview_status = "ready"
    elif thumbnail_url:
        preview_status = "thumbnail_ready"
    else:
        preview_status = str(
            metadata.get("thumbnail_status")
            or metadata.get("proxy_status")
            or "pending"
        )
    return response.model_copy(
        update={
            "preview_url": preview_url,
            "thumbnail_url": thumbnail_url,
            "preview_status": preview_status,
            "labels": list(item.vault_labels or []),
        }
    )


async def _enqueue_default_thumbnail(item: MediaItem) -> None:
    """Queue a small review thumbnail without modifying the original."""

    pool = await _get_redis_pool()
    await pool.enqueue_job(
        "task_build_vault_preview",
        str(item.id),
        False,
        _queue_name="arq:media",
    )


async def _guest_link_or_404(
    db: AsyncSession, token: str, *, require_active: bool = True
) -> MediaVaultGuestLink:
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    result = await db.execute(
        select(MediaVaultGuestLink).where(MediaVaultGuestLink.token_hash == token_hash)
    )
    link = result.scalar_one_or_none()
    if not link:
        raise HTTPException(status_code=404, detail="Upload link not found")
    if require_active and (link.revoked_at or link.expires_at < utc_now()):
        raise HTTPException(status_code=410, detail="This upload link has expired")
    return link


async def _batch_for_actor(
    db: AsyncSession,
    batch_id: uuid.UUID,
    *,
    actor: Optional[VaultActor] = None,
    guest_link: Optional[MediaVaultGuestLink] = None,
) -> MediaUploadBatch:
    result = await db.execute(
        select(MediaUploadBatch).where(MediaUploadBatch.id == batch_id)
    )
    batch = result.scalar_one_or_none()
    if not batch:
        raise HTTPException(status_code=404, detail="Upload batch not found")
    if guest_link:
        if batch.guest_link_id != guest_link.id:
            raise HTTPException(
                status_code=403, detail="Upload link cannot use this batch"
            )
    elif actor:
        vault = await get_vault_or_404(db, batch.vault_id)
        role = await effective_vault_role(db, vault_id=vault.id, actor=actor)
        owns_batch = (
            actor.member_id is not None and batch.uploader_member_id == actor.member_id
        )
        if not actor.is_admin and not owns_batch and ROLE_RANK.get(role or "", 0) < 2:
            raise HTTPException(status_code=403, detail="Batch access denied")
    return batch


async def _create_batch(
    db: AsyncSession,
    *,
    vault: MediaVault,
    payload: UploadBatchCreate,
    auth_id: Optional[uuid.UUID],
    member_id: Optional[uuid.UUID],
    guest_link: Optional[MediaVaultGuestLink],
    bypass_upload_window: bool = False,
) -> MediaUploadBatch:
    await require_upload_window(vault, bypass_time_window=bypass_upload_window)
    if payload.expected_bytes > vault.max_total_bytes - vault.used_bytes:
        raise HTTPException(status_code=413, detail="Vault storage allowance exceeded")
    if guest_link and payload.expected_bytes > (
        guest_link.max_total_bytes - guest_link.used_bytes
    ):
        raise HTTPException(status_code=413, detail="Upload-link allowance exceeded")
    batch = MediaUploadBatch(
        vault_id=vault.id,
        uploader_member_id=member_id,
        uploader_auth_id=auth_id,
        guest_link_id=guest_link.id if guest_link else None,
        expected_files=payload.expected_files,
        expected_bytes=payload.expected_bytes,
        consent_attested_at=utc_now(),
        consent_attestation_text=payload.consent_attestation_text,
        checklist_completed=payload.checklist_completed,
        notes=payload.notes,
    )
    db.add(batch)
    await db.commit()
    await db.refresh(batch)
    return batch


async def _initiate_upload(
    db: AsyncSession,
    *,
    batch: MediaUploadBatch,
    payload: MultipartInitiateRequest,
    uploader_id: uuid.UUID,
    actor_member_id: Optional[uuid.UUID],
    request: Request,
    bypass_upload_window: bool = False,
) -> MultipartInitiateResponse:
    vault = await db.scalar(
        select(MediaVault).where(MediaVault.id == batch.vault_id).with_for_update()
    )
    if not vault:
        raise HTTPException(status_code=404, detail="Media vault not found")
    await require_upload_window(vault, bypass_time_window=bypass_upload_window)
    if batch.status != "open":
        raise HTTPException(status_code=409, detail="Upload batch is not open")
    if payload.size_bytes > vault.max_file_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds this vault's {vault.max_file_bytes}-byte limit",
        )
    pending = await db.scalar(
        select(func.coalesce(func.sum(MediaItem.size_bytes), 0)).where(
            MediaItem.vault_id == vault.id,
            MediaItem.processing_status == "uploading",
        )
    )
    if (
        vault.used_bytes + int(pending or 0) + payload.size_bytes
        > vault.max_total_bytes
    ):
        raise HTTPException(status_code=413, detail="Vault storage allowance exceeded")
    batch_totals = await db.execute(
        select(
            func.coalesce(func.sum(MediaItem.size_bytes), 0),
            func.count(MediaItem.id),
        ).where(
            MediaItem.upload_batch_id == batch.id,
            MediaItem.processing_status.in_(["uploading", "ready"]),
        )
    )
    batch_declared, batch_item_count = batch_totals.one()
    if int(batch_declared or 0) + payload.size_bytes > batch.expected_bytes:
        raise HTTPException(
            status_code=413,
            detail="File exceeds the upload batch's declared byte allowance",
        )
    if int(batch_item_count or 0) >= batch.expected_files:
        raise HTTPException(
            status_code=409,
            detail="Upload batch already has its declared number of files",
        )
    if batch.guest_link_id:
        guest_link = await db.scalar(
            select(MediaVaultGuestLink)
            .where(MediaVaultGuestLink.id == batch.guest_link_id)
            .with_for_update()
        )
        if not guest_link or guest_link.revoked_at or guest_link.expires_at < utc_now():
            raise HTTPException(
                status_code=410, detail="Upload link is no longer active"
            )
        guest_pending = await db.scalar(
            select(func.coalesce(func.sum(MediaItem.size_bytes), 0))
            .join(
                MediaUploadBatch,
                MediaUploadBatch.id == MediaItem.upload_batch_id,
            )
            .where(
                MediaUploadBatch.guest_link_id == guest_link.id,
                MediaItem.processing_status == "uploading",
            )
        )
        if (
            guest_link.used_bytes + int(guest_pending or 0) + payload.size_bytes
            > guest_link.max_total_bytes
        ):
            raise HTTPException(
                status_code=413, detail="Upload-link allowance exceeded"
            )
    if batch.completed_files >= batch.expected_files:
        raise HTTPException(
            status_code=409, detail="Batch file count is already complete"
        )

    media_type = _media_type_for(payload.content_type, payload.filename)
    filename = _safe_filename(payload.filename)
    media_id = uuid.uuid4()
    key = (
        f"vaults/{vault.capture_date.isoformat()}/{vault.id}/"
        f"originals/{media_id}/{filename}"
    )
    upload_id = await storage_service.create_multipart_upload(
        file_key=key,
        content_type=payload.content_type,
        download_name=filename,
        metadata={
            "vault-id": str(vault.id),
            "media-item-id": str(media_id),
            "original-filename": filename[:128],
        },
    )
    duplicate_id = None
    if payload.client_fingerprint:
        duplicate_id = await db.scalar(
            select(MediaItem.id)
            .where(
                MediaItem.vault_id == vault.id,
                MediaItem.client_fingerprint == payload.client_fingerprint,
                MediaItem.size_bytes == payload.size_bytes,
                MediaItem.processing_status == "ready",
                MediaItem.soft_deleted_at.is_(None),
            )
            .limit(1)
        )
    item = MediaItem(
        id=media_id,
        media_type=media_type,
        file_url=_canonical_private_url(key),
        thumbnail_url=None,
        uploaded_by=uploader_id,
        vault_id=vault.id,
        upload_batch_id=batch.id,
        object_key=key,
        bucket_type=BucketType.PRIVATE.value,
        original_filename=filename,
        content_type=payload.content_type,
        size_bytes=payload.size_bytes,
        client_fingerprint=payload.client_fingerprint,
        checksum_sha256=payload.checksum_sha256,
        multipart_upload_id=upload_id,
        captured_at=payload.captured_at,
        processing_status="uploading",
        review_status="unreviewed",
        consent_status="unreviewed",
        duplicate_of_id=duplicate_id,
        is_processed=True,
        metadata_info={"original_preserved": True, "auto_transcoded": False},
    )
    ip, user_agent = _request_metadata(request)
    db.add(item)
    db.add(
        MediaTransferLog(
            vault_id=vault.id,
            media_item_id=media_id,
            object_key=key,
            actor_id=uploader_id,
            actor_member_id=actor_member_id,
            transfer_type="original",
            direction="upload",
            delivery_method="s3_multipart",
            bytes_authorized=payload.size_bytes,
            ip_address=ip,
            user_agent=user_agent,
            metadata_json={"batch_id": str(batch.id)},
        )
    )
    try:
        await db.commit()
    except Exception:
        await storage_service.abort_multipart_upload(file_key=key, upload_id=upload_id)
        raise

    part_size = recommended_multipart_part_size(payload.size_bytes)
    return MultipartInitiateResponse(
        media_item_id=media_id,
        object_key=key,
        upload_id=upload_id,
        part_size=part_size,
        part_count=math.ceil(payload.size_bytes / part_size),
        expires_in_seconds=settings.MEDIA_VAULT_MULTIPART_URL_TTL_SECONDS,
        duplicate_of_id=duplicate_id,
    )


async def _upload_item_for_batch(
    db: AsyncSession, item_id: uuid.UUID, batch: MediaUploadBatch
) -> MediaItem:
    result = await db.execute(
        select(MediaItem).where(
            MediaItem.id == item_id,
            MediaItem.upload_batch_id == batch.id,
            MediaItem.soft_deleted_at.is_(None),
        )
    )
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Upload item not found")
    return item


async def _sign_parts(
    item: MediaItem, payload: MultipartSignPartsRequest
) -> MultipartSignPartsResponse:
    if item.processing_status != "uploading" or not item.multipart_upload_id:
        raise HTTPException(status_code=409, detail="Upload is not active")
    part_size = recommended_multipart_part_size(int(item.size_bytes or 0))
    part_count = math.ceil(int(item.size_bytes or 0) / part_size)
    unique_parts = sorted(set(payload.part_numbers))
    if any(number < 1 or number > part_count for number in unique_parts):
        raise HTTPException(status_code=422, detail="Invalid multipart part number")
    parts = await storage_service.sign_multipart_parts(
        file_key=item.object_key,
        upload_id=item.multipart_upload_id,
        part_numbers=unique_parts,
        expiration=settings.MEDIA_VAULT_MULTIPART_URL_TTL_SECONDS,
    )
    return MultipartSignPartsResponse(
        parts=parts,
        expires_in_seconds=settings.MEDIA_VAULT_MULTIPART_URL_TTL_SECONDS,
    )


async def _complete_upload(
    db: AsyncSession,
    *,
    item: MediaItem,
    batch: MediaUploadBatch,
    payload: MultipartCompleteRequest,
    guest_link: Optional[MediaVaultGuestLink] = None,
) -> VaultMediaResponse:
    if item.processing_status == "ready":
        return await _media_response(item)
    if item.processing_status != "uploading" or not item.multipart_upload_id:
        raise HTTPException(status_code=409, detail="Upload is not active")
    sorted_parts = sorted(payload.parts, key=lambda part: part.part_number)
    if len({part.part_number for part in sorted_parts}) != len(sorted_parts):
        raise HTTPException(status_code=422, detail="Duplicate part numbers")
    await storage_service.complete_multipart_upload(
        file_key=item.object_key,
        upload_id=item.multipart_upload_id,
        parts=[
            {"PartNumber": part.part_number, "ETag": part.etag} for part in sorted_parts
        ],
    )
    metadata = await storage_service.head_object(item.object_key, BucketType.PRIVATE)
    actual_size = int(metadata["size_bytes"])
    if actual_size != item.size_bytes:
        item.processing_status = "failed"
        await db.commit()
        raise HTTPException(
            status_code=409,
            detail="Uploaded object size did not match the declared original",
        )

    locked_batch = await db.scalar(
        select(MediaUploadBatch)
        .where(MediaUploadBatch.id == batch.id)
        .with_for_update()
    )
    if not locked_batch:
        raise HTTPException(status_code=409, detail="Upload batch no longer exists")
    batch = locked_batch
    item.processing_status = "ready"
    item.multipart_upload_id = None
    item.metadata_info = {
        **(item.metadata_info or {}),
        "etag": metadata.get("etag"),
        "original_preserved": True,
        "auto_transcoded": False,
        "thumbnail_status": "pending",
    }
    batch.completed_files += 1
    batch.completed_bytes += actual_size
    if batch.completed_files >= batch.expected_files:
        batch.status = "complete"
    vault = await db.scalar(
        select(MediaVault).where(MediaVault.id == batch.vault_id).with_for_update()
    )
    if not vault:
        raise HTTPException(status_code=409, detail="Media vault no longer exists")
    vault.used_bytes += actual_size
    if guest_link:
        locked_link = await db.scalar(
            select(MediaVaultGuestLink)
            .where(MediaVaultGuestLink.id == guest_link.id)
            .with_for_update()
        )
        if locked_link:
            locked_link.used_bytes += actual_size
    transfer = await db.scalar(
        select(MediaTransferLog)
        .where(
            MediaTransferLog.media_item_id == item.id,
            MediaTransferLog.direction == "upload",
        )
        .order_by(desc(MediaTransferLog.created_at))
        .limit(1)
    )
    if transfer:
        transfer.status = "completed"
        transfer.bytes_transferred = actual_size
        transfer.measurement_source = "s3_head"
        transfer.completed_at = utc_now()
    await db.commit()
    await db.refresh(item)
    try:
        await _enqueue_default_thumbnail(item)
    except Exception:
        # A derivative must never make a successful original upload fail. The
        # item list retries missing thumbnails when a curator opens the vault.
        logger.exception("Could not queue default vault thumbnail for %s", item.id)
        metadata = dict(item.metadata_info or {})
        metadata["thumbnail_status"] = "failed"
        item.metadata_info = metadata
        await db.commit()
    if batch.status == "complete":
        curator_ids = list(
            (
                await db.execute(
                    select(MediaVaultGrant.member_id).where(
                        MediaVaultGrant.vault_id == vault.id,
                        MediaVaultGrant.role.in_(["curator", "admin"]),
                        MediaVaultGrant.revoked_at.is_(None),
                        MediaVaultGrant.expires_at >= utc_now(),
                    )
                )
            )
            .scalars()
            .all()
        )
        await dispatch_notification(
            type="media_vault_batch_ready",
            category="media",
            member_ids=[str(member_id) for member_id in curator_ids],
            title=f"New media is ready in {vault.title}",
            body=f"{batch.completed_files} full-quality files are ready to review.",
            action_url=f"/account/media-vault/{vault.id}",
            calling_service="media",
        )
    return await _media_response(item)


async def _abort_upload(db: AsyncSession, *, item: MediaItem) -> None:
    if item.processing_status == "uploading" and item.multipart_upload_id:
        await storage_service.abort_multipart_upload(
            file_key=item.object_key,
            upload_id=item.multipart_upload_id,
        )
        item.multipart_upload_id = None
        item.processing_status = "aborted"
        transfer = await db.scalar(
            select(MediaTransferLog)
            .where(
                MediaTransferLog.media_item_id == item.id,
                MediaTransferLog.direction == "upload",
                MediaTransferLog.status == "authorized",
            )
            .order_by(desc(MediaTransferLog.created_at))
            .limit(1)
        )
        if transfer:
            transfer.status = "aborted"
            transfer.completed_at = utc_now()
        await db.commit()


# Guest routes are declared before /{vault_id} routes to avoid path ambiguity.


@router.get("/guest/{token}", response_model=GuestVaultResponse)
async def get_guest_vault(token: str, db: AsyncSession = Depends(get_async_db)):
    link = await _guest_link_or_404(db, token)
    vault = await get_vault_or_404(db, link.vault_id)
    await require_upload_window(vault)
    return GuestVaultResponse(
        vault_id=vault.id,
        title=vault.title,
        capture_date=vault.capture_date,
        location_name=vault.location_name,
        upload_closes_at=vault.upload_closes_at,
        max_file_bytes=vault.max_file_bytes,
        remaining_bytes=max(0, link.max_total_bytes - link.used_bytes),
        consent_notice=vault.consent_notice,
        shot_checklist=vault.shot_checklist,
        link_label=link.label,
    )


@router.post(
    "/guest/{token}/batches",
    response_model=UploadBatchResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_guest_batch(
    token: str,
    payload: UploadBatchCreate,
    db: AsyncSession = Depends(get_async_db),
):
    link = await _guest_link_or_404(db, token)
    vault = await get_vault_or_404(db, link.vault_id)
    return await _create_batch(
        db,
        vault=vault,
        payload=payload,
        auth_id=None,
        member_id=None,
        guest_link=link,
    )


@router.post(
    "/guest/{token}/batches/{batch_id}/uploads/initiate",
    response_model=MultipartInitiateResponse,
)
async def initiate_guest_upload(
    token: str,
    batch_id: uuid.UUID,
    payload: MultipartInitiateRequest,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
):
    link = await _guest_link_or_404(db, token)
    batch = await _batch_for_actor(db, batch_id, guest_link=link)
    return await _initiate_upload(
        db,
        batch=batch,
        payload=payload,
        uploader_id=link.id,
        actor_member_id=None,
        request=request,
    )


@router.post(
    "/guest/{token}/batches/{batch_id}/uploads/{item_id}/parts",
    response_model=MultipartSignPartsResponse,
)
async def sign_guest_parts(
    token: str,
    batch_id: uuid.UUID,
    item_id: uuid.UUID,
    payload: MultipartSignPartsRequest,
    db: AsyncSession = Depends(get_async_db),
):
    link = await _guest_link_or_404(db, token)
    batch = await _batch_for_actor(db, batch_id, guest_link=link)
    item = await _upload_item_for_batch(db, item_id, batch)
    return await _sign_parts(item, payload)


@router.get("/guest/{token}/batches/{batch_id}/uploads/{item_id}/parts")
async def resume_guest_parts(
    token: str,
    batch_id: uuid.UUID,
    item_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    link = await _guest_link_or_404(db, token)
    batch = await _batch_for_actor(db, batch_id, guest_link=link)
    item = await _upload_item_for_batch(db, item_id, batch)
    if not item.multipart_upload_id:
        return {"parts": [], "status": item.processing_status}
    parts = await storage_service.list_multipart_parts(
        file_key=item.object_key, upload_id=item.multipart_upload_id
    )
    return {"parts": parts, "status": item.processing_status}


@router.post(
    "/guest/{token}/batches/{batch_id}/uploads/{item_id}/complete",
    response_model=VaultMediaResponse,
)
async def complete_guest_upload(
    token: str,
    batch_id: uuid.UUID,
    item_id: uuid.UUID,
    payload: MultipartCompleteRequest,
    db: AsyncSession = Depends(get_async_db),
):
    link = await _guest_link_or_404(db, token)
    batch = await _batch_for_actor(db, batch_id, guest_link=link)
    item = await _upload_item_for_batch(db, item_id, batch)
    return await _complete_upload(
        db, item=item, batch=batch, payload=payload, guest_link=link
    )


@router.delete(
    "/guest/{token}/batches/{batch_id}/uploads/{item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def abort_guest_upload(
    token: str,
    batch_id: uuid.UUID,
    item_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    link = await _guest_link_or_404(db, token)
    batch = await _batch_for_actor(db, batch_id, guest_link=link)
    item = await _upload_item_for_batch(db, item_id, batch)
    await _abort_upload(db, item=item)


@router.get("", response_model=VaultListResponse)
async def list_vaults(
    capture_from: Optional[datetime] = None,
    capture_to: Optional[datetime] = None,
    status_filter: Optional[str] = Query(default=None, alias="status"),
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    actor = await resolve_actor(current_user)
    query = select(MediaVault)
    if not actor.is_admin:
        if not actor.member_id:
            return VaultListResponse(items=[], total=0)
        query = query.join(MediaVaultGrant).where(
            MediaVaultGrant.member_id == actor.member_id,
            MediaVaultGrant.revoked_at.is_(None),
            MediaVaultGrant.starts_at <= utc_now(),
            MediaVaultGrant.expires_at >= utc_now(),
        )
    if capture_from:
        query = query.where(MediaVault.capture_date >= capture_from.date())
    if capture_to:
        query = query.where(MediaVault.capture_date <= capture_to.date())
    if status_filter:
        query = query.where(MediaVault.status == status_filter)
    result = await db.execute(
        query.distinct().order_by(desc(MediaVault.capture_date), MediaVault.title)
    )
    vaults = list(result.scalars().all())
    return VaultListResponse(
        items=[await _vault_response(db, vault, actor) for vault in vaults],
        total=len(vaults),
    )


@router.post("", response_model=VaultResponse, status_code=201)
async def create_vault(
    payload: VaultCreate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    actor = await resolve_actor(current_user)
    values = payload.model_dump()
    if payload.ends_at:
        values["upload_closes_at"] = max(
            payload.upload_closes_at,
            payload.ends_at + timedelta(hours=MEDIA_VAULT_UPLOAD_WINDOW_HOURS),
        )
    if not values["shot_checklist"]:
        values["shot_checklist"] = list(DEFAULT_MEDIA_VAULT_CHECKLIST)
    if not values["consent_notice"]:
        values["consent_notice"] = DEFAULT_MEDIA_VAULT_CONSENT_NOTICE
    current = utc_now()
    initial_status = "scheduled"
    if values["upload_opens_at"] <= current <= values["upload_closes_at"]:
        initial_status = "open"
    elif current > values["upload_closes_at"]:
        initial_status = "review"
    vault = MediaVault(
        **values,
        status=initial_status,
        auto_transcode=False,
        settings_json=default_media_coverage_settings(),
        created_by=actor.auth_id,
    )
    db.add(vault)
    try:
        await db.commit()
    except Exception as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="A media vault already exists for this session or event",
        ) from exc
    await db.refresh(vault)
    if vault.session_id or vault.event_id:
        try:
            await sync_vault_volunteer_grants(db, vault=vault, created_by=actor.auth_id)
        except Exception:
            # Volunteer synchronization is retryable from the vault admin screen.
            await db.rollback()
            vault = await get_vault_or_404(db, vault.id)
    return await _vault_response(db, vault, actor)


@router.get("/{vault_id}", response_model=VaultResponse)
async def get_vault(
    vault_id: uuid.UUID,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    actor = await resolve_actor(current_user)
    vault = await get_vault_or_404(db, vault_id)
    await require_vault_role(db, vault=vault, actor=actor, minimum="contributor")
    return await _vault_response(db, vault, actor)


@router.patch("/{vault_id}", response_model=VaultResponse)
async def update_vault(
    vault_id: uuid.UUID,
    payload: VaultUpdate,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    actor = await resolve_actor(current_user)
    vault = await get_vault_or_404(db, vault_id)
    await require_vault_role(db, vault=vault, actor=actor, minimum="admin")
    values = payload.model_dump(exclude_unset=True)
    for key, value in values.items():
        setattr(vault, key, value)
    if vault.upload_closes_at <= vault.upload_opens_at:
        raise HTTPException(status_code=422, detail="Invalid upload window")
    await db.commit()
    await db.refresh(vault)
    return await _vault_response(db, vault, actor)


@router.get("/{vault_id}/grants", response_model=list[VaultGrantResponse])
async def list_grants(
    vault_id: uuid.UUID,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    actor = await resolve_actor(current_user)
    vault = await get_vault_or_404(db, vault_id)
    await require_vault_role(db, vault=vault, actor=actor, minimum="admin")
    rows = await db.execute(
        select(MediaVaultGrant)
        .where(MediaVaultGrant.vault_id == vault_id)
        .order_by(desc(MediaVaultGrant.created_at))
    )
    return list(rows.scalars().all())


@router.post(
    "/{vault_id}/grants/sync-volunteers",
    response_model=list[VaultGrantResponse],
)
async def sync_volunteer_grants(
    vault_id: uuid.UUID,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    actor = await resolve_actor(current_user)
    vault = await get_vault_or_404(db, vault_id)
    await require_vault_role(db, vault=vault, actor=actor, minimum="admin")
    return await sync_vault_volunteer_grants(db, vault=vault, created_by=actor.auth_id)


@router.post("/{vault_id}/grants", response_model=VaultGrantResponse, status_code=201)
async def create_grant(
    vault_id: uuid.UUID,
    payload: VaultGrantCreate,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    actor = await resolve_actor(current_user)
    vault = await get_vault_or_404(db, vault_id)
    await require_vault_role(db, vault=vault, actor=actor, minimum="admin")
    if vault.status == "archived":
        raise HTTPException(
            status_code=409, detail="Archived vaults cannot be reopened"
        )
    starts_at = payload.starts_at
    expires_at = payload.expires_at
    if payload.role == "contributor":
        starts_at, expires_at = ensure_contributor_window(
            vault,
            starts_at=payload.starts_at,
            expires_at=payload.expires_at,
        )
    existing = await db.scalar(
        select(MediaVaultGrant).where(
            MediaVaultGrant.vault_id == vault_id,
            MediaVaultGrant.member_id == payload.member_id,
            MediaVaultGrant.role == payload.role,
        )
    )
    if existing:
        existing.starts_at = starts_at
        existing.expires_at = expires_at
        existing.can_download_originals = payload.can_download_originals
        existing.revoked_at = None
        grant = existing
    else:
        grant = MediaVaultGrant(
            vault_id=vault_id,
            member_id=payload.member_id,
            role=payload.role,
            starts_at=starts_at,
            expires_at=expires_at,
            can_download_originals=payload.can_download_originals,
            source="manual",
            created_by=actor.auth_id,
        )
        db.add(grant)
    await db.commit()
    await db.refresh(grant)
    notification_result = await notify_vault_access(
        vault=vault,
        member_id=payload.member_id,
        role=payload.role,
        expires_at=expires_at,
    )
    response = VaultGrantResponse.model_validate(grant)
    return response.model_copy(
        update={"notification_dispatched": notification_result is not None}
    )


@router.delete("/{vault_id}/grants/{grant_id}", status_code=204)
async def revoke_grant(
    vault_id: uuid.UUID,
    grant_id: uuid.UUID,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    actor = await resolve_actor(current_user)
    vault = await get_vault_or_404(db, vault_id)
    await require_vault_role(db, vault=vault, actor=actor, minimum="admin")
    grant = await db.scalar(
        select(MediaVaultGrant).where(
            MediaVaultGrant.id == grant_id,
            MediaVaultGrant.vault_id == vault_id,
        )
    )
    if not grant:
        raise HTTPException(status_code=404, detail="Grant not found")
    grant.revoked_at = utc_now()
    await db.commit()


@router.post(
    "/{vault_id}/guest-links", response_model=GuestLinkResponse, status_code=201
)
async def create_guest_link(
    vault_id: uuid.UUID,
    payload: GuestLinkCreate,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    actor = await resolve_actor(current_user)
    vault = await get_vault_or_404(db, vault_id)
    await require_vault_role(db, vault=vault, actor=actor, minimum="admin")
    if vault.status == "archived":
        raise HTTPException(
            status_code=409, detail="Archived vaults cannot be reopened"
        )
    if payload.expires_at <= utc_now():
        raise HTTPException(status_code=422, detail="Expiry must be in the future")
    vault.upload_opens_at = min(vault.upload_opens_at, utc_now())
    vault.upload_closes_at = max(vault.upload_closes_at, payload.expires_at)
    vault.status = "open"
    raw_token = secrets.token_urlsafe(32)
    link = MediaVaultGuestLink(
        vault_id=vault_id,
        token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
        label=payload.label,
        expires_at=payload.expires_at,
        max_total_bytes=payload.max_total_bytes,
        created_by=actor.auth_id,
    )
    db.add(link)
    await db.commit()
    await db.refresh(link)
    response = GuestLinkResponse.model_validate(link)
    return response.model_copy(
        update={
            "upload_url": (
                f"{settings.FRONTEND_URL.rstrip('/')}/media-vault/upload/{raw_token}"
            )
        }
    )


@router.get("/{vault_id}/guest-links", response_model=list[GuestLinkResponse])
async def list_guest_links(
    vault_id: uuid.UUID,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    actor = await resolve_actor(current_user)
    vault = await get_vault_or_404(db, vault_id)
    await require_vault_role(db, vault=vault, actor=actor, minimum="admin")
    rows = await db.execute(
        select(MediaVaultGuestLink)
        .where(MediaVaultGuestLink.vault_id == vault_id)
        .order_by(desc(MediaVaultGuestLink.created_at))
    )
    return list(rows.scalars().all())


@router.delete("/{vault_id}/guest-links/{link_id}", status_code=204)
async def revoke_guest_link(
    vault_id: uuid.UUID,
    link_id: uuid.UUID,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    actor = await resolve_actor(current_user)
    vault = await get_vault_or_404(db, vault_id)
    await require_vault_role(db, vault=vault, actor=actor, minimum="admin")
    link = await db.scalar(
        select(MediaVaultGuestLink).where(
            MediaVaultGuestLink.id == link_id,
            MediaVaultGuestLink.vault_id == vault_id,
        )
    )
    if not link:
        raise HTTPException(status_code=404, detail="Guest link not found")
    link.revoked_at = utc_now()
    await db.commit()


@router.post("/{vault_id}/batches", response_model=UploadBatchResponse, status_code=201)
async def create_member_batch(
    vault_id: uuid.UUID,
    payload: UploadBatchCreate,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    actor = await resolve_actor(current_user)
    vault = await get_vault_or_404(db, vault_id)
    await require_vault_role(db, vault=vault, actor=actor, minimum="contributor")
    return await _create_batch(
        db,
        vault=vault,
        payload=payload,
        auth_id=actor.auth_id,
        member_id=actor.member_id,
        guest_link=None,
        bypass_upload_window=actor.is_admin,
    )


@router.post(
    "/{vault_id}/batches/{batch_id}/uploads/initiate",
    response_model=MultipartInitiateResponse,
)
async def initiate_member_upload(
    vault_id: uuid.UUID,
    batch_id: uuid.UUID,
    payload: MultipartInitiateRequest,
    request: Request,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    actor = await resolve_actor(current_user)
    vault = await get_vault_or_404(db, vault_id)
    await require_vault_role(db, vault=vault, actor=actor, minimum="contributor")
    batch = await _batch_for_actor(db, batch_id, actor=actor)
    if batch.vault_id != vault_id:
        raise HTTPException(status_code=404, detail="Batch not found in this vault")
    return await _initiate_upload(
        db,
        batch=batch,
        payload=payload,
        uploader_id=actor.auth_id,
        actor_member_id=actor.member_id,
        request=request,
        bypass_upload_window=actor.is_admin,
    )


@router.post(
    "/{vault_id}/batches/{batch_id}/uploads/{item_id}/parts",
    response_model=MultipartSignPartsResponse,
)
async def sign_member_parts(
    vault_id: uuid.UUID,
    batch_id: uuid.UUID,
    item_id: uuid.UUID,
    payload: MultipartSignPartsRequest,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    actor = await resolve_actor(current_user)
    batch = await _batch_for_actor(db, batch_id, actor=actor)
    if batch.vault_id != vault_id:
        raise HTTPException(status_code=404, detail="Batch not found in this vault")
    item = await _upload_item_for_batch(db, item_id, batch)
    return await _sign_parts(item, payload)


@router.get("/{vault_id}/batches/{batch_id}/uploads/{item_id}/parts")
async def resume_member_parts(
    vault_id: uuid.UUID,
    batch_id: uuid.UUID,
    item_id: uuid.UUID,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    actor = await resolve_actor(current_user)
    batch = await _batch_for_actor(db, batch_id, actor=actor)
    if batch.vault_id != vault_id:
        raise HTTPException(status_code=404, detail="Batch not found in this vault")
    item = await _upload_item_for_batch(db, item_id, batch)
    if not item.multipart_upload_id:
        return {"parts": [], "status": item.processing_status}
    parts = await storage_service.list_multipart_parts(
        file_key=item.object_key, upload_id=item.multipart_upload_id
    )
    return {"parts": parts, "status": item.processing_status}


@router.post(
    "/{vault_id}/batches/{batch_id}/uploads/{item_id}/complete",
    response_model=VaultMediaResponse,
)
async def complete_member_upload(
    vault_id: uuid.UUID,
    batch_id: uuid.UUID,
    item_id: uuid.UUID,
    payload: MultipartCompleteRequest,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    actor = await resolve_actor(current_user)
    batch = await _batch_for_actor(db, batch_id, actor=actor)
    if batch.vault_id != vault_id:
        raise HTTPException(status_code=404, detail="Batch not found in this vault")
    item = await _upload_item_for_batch(db, item_id, batch)
    return await _complete_upload(db, item=item, batch=batch, payload=payload)


@router.delete("/{vault_id}/batches/{batch_id}/uploads/{item_id}", status_code=204)
async def abort_member_upload(
    vault_id: uuid.UUID,
    batch_id: uuid.UUID,
    item_id: uuid.UUID,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    actor = await resolve_actor(current_user)
    batch = await _batch_for_actor(db, batch_id, actor=actor)
    if batch.vault_id != vault_id:
        raise HTTPException(status_code=404, detail="Batch not found in this vault")
    item = await _upload_item_for_batch(db, item_id, batch)
    await _abort_upload(db, item=item)


@router.get("/{vault_id}/items", response_model=VaultMediaListResponse)
async def list_vault_items(
    vault_id: uuid.UUID,
    review_status: Optional[str] = None,
    consent_status: Optional[str] = None,
    media_type: Optional[str] = None,
    processing_status: str = "ready",
    label: Optional[str] = None,
    search: Optional[str] = None,
    duplicate_only: bool = False,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=60, ge=1, le=200),
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    actor = await resolve_actor(current_user)
    vault = await get_vault_or_404(db, vault_id)
    role = await require_vault_role(db, vault=vault, actor=actor, minimum="contributor")
    allowed_processing_states = {"ready", "uploading", "failed", "aborted", "all"}
    if processing_status not in allowed_processing_states:
        raise HTTPException(status_code=422, detail="Invalid processing status")
    conditions = [MediaItem.vault_id == vault_id, MediaItem.soft_deleted_at.is_(None)]
    if processing_status != "all":
        conditions.append(MediaItem.processing_status == processing_status)
    elif not actor.is_admin:
        conditions.append(MediaItem.processing_status == "ready")
    if role == "contributor" and not actor.is_admin:
        conditions.append(MediaItem.uploaded_by == actor.auth_id)
    if review_status:
        conditions.append(MediaItem.review_status == review_status)
    if consent_status:
        conditions.append(MediaItem.consent_status == consent_status)
    if media_type:
        conditions.append(MediaItem.media_type == media_type.upper())
    if label:
        conditions.append(MediaItem.vault_labels.contains([label.strip()]))
    if search:
        term = f"%{search.strip()}%"
        conditions.append(
            or_(
                MediaItem.original_filename.ilike(term),
                cast(MediaItem.vault_labels, String).ilike(term),
            )
        )
    if duplicate_only:
        conditions.append(MediaItem.duplicate_of_id.is_not(None))
    total = int(
        await db.scalar(select(func.count(MediaItem.id)).where(*conditions)) or 0
    )
    result = await db.execute(
        select(MediaItem)
        .where(*conditions)
        .order_by(desc(func.coalesce(MediaItem.captured_at, MediaItem.created_at)))
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = list(result.scalars())
    missing_thumbnails: list[MediaItem] = []
    for item in items:
        if item.processing_status != "ready" or item.thumbnail_object_key:
            continue
        metadata = dict(item.metadata_info or {})
        if metadata.get("thumbnail_status") in {"pending", "processing"}:
            continue
        metadata["thumbnail_status"] = "pending"
        item.metadata_info = metadata
        missing_thumbnails.append(item)
    if missing_thumbnails:
        await db.commit()
        queue_failed = False
        for item in missing_thumbnails:
            try:
                await _enqueue_default_thumbnail(item)
            except Exception:
                logger.exception("Could not queue vault thumbnail for %s", item.id)
                metadata = dict(item.metadata_info or {})
                metadata["thumbnail_status"] = "failed"
                item.metadata_info = metadata
                queue_failed = True
        if queue_failed:
            await db.commit()
    return VaultMediaListResponse(
        items=[await _media_response(item) for item in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.patch("/{vault_id}/items/{item_id}", response_model=VaultMediaResponse)
async def review_item(
    vault_id: uuid.UUID,
    item_id: uuid.UUID,
    payload: ReviewUpdate,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    actor = await resolve_actor(current_user)
    vault = await get_vault_or_404(db, vault_id)
    await require_vault_role(db, vault=vault, actor=actor, minimum="curator")
    item = await db.scalar(
        select(MediaItem).where(MediaItem.id == item_id, MediaItem.vault_id == vault_id)
    )
    if not item:
        raise HTTPException(status_code=404, detail="Vault media not found")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(item, "vault_labels" if key == "labels" else key, value)
    item.reviewed_by = actor.auth_id
    item.reviewed_at = utc_now()
    await db.commit()
    await db.refresh(item)
    return await _media_response(item)


@router.patch("/{vault_id}/items", response_model=list[VaultMediaResponse])
async def bulk_review_items(
    vault_id: uuid.UUID,
    payload: BulkReviewRequest,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    actor = await resolve_actor(current_user)
    vault = await get_vault_or_404(db, vault_id)
    await require_vault_role(db, vault=vault, actor=actor, minimum="curator")
    rows = await db.execute(
        select(MediaItem).where(
            MediaItem.vault_id == vault_id,
            MediaItem.id.in_(payload.media_item_ids),
            MediaItem.soft_deleted_at.is_(None),
        )
    )
    items = list(rows.scalars().all())
    updates = payload.model_dump(exclude={"media_item_ids"}, exclude_unset=True)
    for item in items:
        for key, value in updates.items():
            setattr(item, "vault_labels" if key == "labels" else key, value)
        item.reviewed_by = actor.auth_id
        item.reviewed_at = utc_now()
    await db.commit()
    return [await _media_response(item) for item in items]


@router.post(
    "/{vault_id}/items/delete",
    response_model=VaultMediaDeleteResponse,
)
async def delete_vault_items(
    vault_id: uuid.UUID,
    payload: VaultMediaDeleteRequest,
    request: Request,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Remove vault media, optionally deleting every stored copy permanently."""

    vault = await get_vault_or_404(db, vault_id)
    rows = await db.execute(
        select(MediaItem).where(
            MediaItem.vault_id == vault_id,
            MediaItem.id.in_(payload.media_item_ids),
            MediaItem.soft_deleted_at.is_(None),
        )
    )
    items = list(rows.scalars().all())
    if len(items) != len(set(payload.media_item_ids)):
        raise HTTPException(
            status_code=404, detail="One or more vault items are missing"
        )

    deleted_at = utc_now()
    bytes_deleted = 0
    for item in items:
        previous = {
            "filename": item.original_filename,
            "size_bytes": item.size_bytes,
            "processing_status": item.processing_status,
            "labels": list(item.vault_labels or []),
            "published_media_id": (
                str(item.published_media_id) if item.published_media_id else None
            ),
        }
        if item.multipart_upload_id:
            await storage_service.abort_multipart_upload(
                file_key=item.object_key,
                upload_id=item.multipart_upload_id,
            )
            item.multipart_upload_id = None

        if payload.delete_from_storage:
            for key in {
                item.object_key,
                item.proxy_object_key,
                item.thumbnail_object_key,
            }:
                if key:
                    await storage_service.delete_media(
                        key,
                        bucket_type=BucketType.PRIVATE,
                        is_key=True,
                        strict=True,
                    )
            if item.processing_status == "ready":
                bytes_deleted += int(item.size_bytes or 0)

            if item.published_media_id:
                published = await db.get(MediaItem, item.published_media_id)
                if published and not published.soft_deleted_at:
                    album_items = await db.execute(
                        select(AlbumItem).where(AlbumItem.media_item_id == published.id)
                    )
                    for album_item in album_items.scalars().all():
                        await db.delete(album_item)
                    await storage_service.delete_media(
                        published.file_url,
                        published.thumbnail_url,
                        bucket_type=BucketType.PUBLIC,
                        strict=True,
                    )
                    published.soft_deleted_at = deleted_at
                item.published_media_id = None
                item.published_at = None

        item.soft_deleted_at = deleted_at
        item.metadata_info = {
            **(item.metadata_info or {}),
            "vault_delete_mode": (
                "permanent" if payload.delete_from_storage else "vault_only"
            ),
            "vault_deleted_at": deleted_at.isoformat(),
        }
        await write_audit(
            db,
            action=(
                "media.vault.delete_permanently"
                if payload.delete_from_storage
                else "media.vault.remove"
            ),
            actor=current_user,
            entity_id=item.id,
            request=request,
            old_value=previous,
            new_value={
                "soft_deleted_at": deleted_at.isoformat(),
                "storage_deleted": payload.delete_from_storage,
            },
        )

    if payload.delete_from_storage:
        vault.used_bytes = max(0, int(vault.used_bytes or 0) - bytes_deleted)
    await db.commit()
    return VaultMediaDeleteResponse(
        removed_count=len(items),
        storage_deleted_count=len(items) if payload.delete_from_storage else 0,
        bytes_deleted=bytes_deleted,
    )


@router.post("/{vault_id}/publish", response_model=list[VaultMediaResponse])
async def publish_items(
    vault_id: uuid.UUID,
    payload: PublishRequest,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    actor = await resolve_actor(current_user)
    vault = await get_vault_or_404(db, vault_id)
    await require_vault_role(db, vault=vault, actor=actor, minimum="curator")
    rows = await db.execute(
        select(MediaItem).where(
            MediaItem.vault_id == vault_id,
            MediaItem.id.in_(payload.media_item_ids),
            MediaItem.processing_status == "ready",
            MediaItem.soft_deleted_at.is_(None),
        )
    )
    items = list(rows.scalars().all())
    invalid = [
        item.id
        for item in items
        if item.review_status not in {"approved", "shortlisted"}
        or item.consent_status != "cleared"
    ]
    if invalid:
        raise HTTPException(
            status_code=409,
            detail=(
                "Every published item must be approved/shortlisted and "
                "consent-cleared"
            ),
        )
    if not vault.published_album_id:
        album_type = (
            AlbumType.SESSION
            if vault.session_id
            else AlbumType.EVENT
            if vault.event_id
            else AlbumType.GENERAL
        )
        linked_entity_type = (
            "session" if vault.session_id else "event" if vault.event_id else None
        )
        album = Album(
            title=payload.album_title or vault.title,
            description=vault.description,
            album_type=album_type,
            linked_entity_id=vault.session_id or vault.event_id,
            linked_entity_type=linked_entity_type,
            is_public=payload.make_album_public,
            created_by=actor.auth_id,
        )
        db.add(album)
        await db.flush()
        vault.published_album_id = album.id
    else:
        album = await db.get(Album, vault.published_album_id)
        if album and payload.make_album_public:
            album.is_public = True
    max_order = int(
        await db.scalar(
            select(func.coalesce(func.max(AlbumItem.order), 0)).where(
                AlbumItem.album_id == vault.published_album_id
            )
        )
        or 0
    )
    for item in items:
        if item.published_media_id:
            continue
        public_key = (
            f"gallery/vaults/{vault.id}/{item.id}/"
            f"{_safe_filename(item.original_filename or str(item.id))}"
        )
        public_url = await storage_service.copy_private_to_public(
            source_key=item.object_key,
            destination_key=public_key,
            content_type=item.content_type or "application/octet-stream",
        )
        published = MediaItem(
            media_type=item.media_type,
            file_url=public_url,
            thumbnail_url=None,
            title=item.title or item.original_filename,
            description=item.description,
            alt_text=item.alt_text,
            metadata_info={
                **(item.metadata_info or {}),
                "source_vault_id": str(vault.id),
                "source_media_id": str(item.id),
            },
            is_processed=True,
            uploaded_by=actor.auth_id,
            vault_labels=list(item.vault_labels or []),
        )
        db.add(published)
        await db.flush()
        max_order += 1
        db.add(
            AlbumItem(
                album_id=vault.published_album_id,
                media_item_id=published.id,
                order=max_order,
            )
        )
        item.published_media_id = published.id
        item.published_at = utc_now()
        item.review_status = "published"
    vault.status = "published"
    await db.commit()
    return [await _media_response(item) for item in items]


@router.post(
    "/{vault_id}/items/{item_id}/preview/generate",
    status_code=status.HTTP_202_ACCEPTED,
)
async def request_item_preview(
    vault_id: uuid.UUID,
    item_id: uuid.UUID,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Queue an optional review derivative; never touches the original object."""
    actor = await resolve_actor(current_user)
    vault = await get_vault_or_404(db, vault_id)
    await require_vault_role(db, vault=vault, actor=actor, minimum="curator")
    item = await db.scalar(
        select(MediaItem).where(
            MediaItem.id == item_id,
            MediaItem.vault_id == vault_id,
            MediaItem.processing_status == "ready",
            MediaItem.soft_deleted_at.is_(None),
        )
    )
    if not item:
        raise HTTPException(status_code=404, detail="Vault media not found")
    is_video = item.media_type == MediaType.VIDEO
    if (is_video and item.proxy_object_key) or (
        not is_video and item.thumbnail_object_key
    ):
        return {"status": "ready"}
    metadata = dict(item.metadata_info or {})
    status_key = "proxy_status" if is_video else "thumbnail_status"
    if metadata.get(status_key) not in {"pending", "processing"}:
        metadata[status_key] = "pending"
        item.metadata_info = metadata
        await db.commit()
        pool = await _get_redis_pool()
        await pool.enqueue_job(
            "task_build_vault_preview",
            str(item.id),
            is_video,
            _queue_name="arq:media",
        )
    return {"status": metadata.get(status_key, "pending")}


@router.post(
    "/{vault_id}/items/{item_id}/download",
    response_model=DownloadAuthorizationResponse,
)
async def authorize_item_download(
    vault_id: uuid.UUID,
    item_id: uuid.UUID,
    request: Request,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    actor = await resolve_actor(current_user)
    vault = await get_vault_or_404(db, vault_id)
    role = await require_vault_role(db, vault=vault, actor=actor, minimum="contributor")
    item = await db.scalar(
        select(MediaItem).where(
            MediaItem.id == item_id,
            MediaItem.vault_id == vault_id,
            MediaItem.processing_status == "ready",
            MediaItem.soft_deleted_at.is_(None),
        )
    )
    if not item:
        raise HTTPException(status_code=404, detail="Vault media not found")
    if role == "contributor" and item.uploaded_by != actor.auth_id:
        raise HTTPException(status_code=403, detail="Original download denied")
    ttl = settings.MEDIA_VAULT_SIGNED_URL_TTL_SECONDS
    url, delivery_method = await storage_service.generate_private_download_url(
        file_key=item.object_key,
        expiration=ttl,
        download_name=item.original_filename or str(item.id),
    )
    ip, user_agent = _request_metadata(request)
    transfer = MediaTransferLog(
        vault_id=vault_id,
        media_item_id=item.id,
        object_key=item.object_key,
        actor_id=actor.auth_id,
        actor_member_id=actor.member_id,
        transfer_type="original",
        direction="download",
        delivery_method=delivery_method,
        bytes_authorized=int(item.size_bytes or 0),
        ip_address=ip,
        user_agent=user_agent,
    )
    db.add(transfer)
    await db.commit()
    await db.refresh(transfer)
    return DownloadAuthorizationResponse(
        transfer_id=transfer.id,
        url=url,
        expires_in_seconds=ttl,
        bytes_authorized=int(item.size_bytes or 0),
        filename=item.original_filename or str(item.id),
    )


@router.post("/{vault_id}/transfers/{transfer_id}/complete", status_code=204)
async def complete_transfer_measurement(
    vault_id: uuid.UUID,
    transfer_id: uuid.UUID,
    payload: TransferCompletionRequest,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    actor = await resolve_actor(current_user)
    transfer = await db.scalar(
        select(MediaTransferLog).where(
            MediaTransferLog.id == transfer_id,
            MediaTransferLog.vault_id == vault_id,
        )
    )
    if not transfer:
        raise HTTPException(status_code=404, detail="Transfer not found")
    if transfer.actor_id != actor.auth_id and not actor.is_admin:
        raise HTTPException(status_code=403, detail="Transfer access denied")
    transfer.status = "completed" if payload.succeeded else "failed"
    transfer.bytes_transferred = min(
        payload.bytes_transferred, transfer.bytes_authorized
    )
    transfer.measurement_source = "client_callback"
    transfer.completed_at = utc_now()
    await db.commit()


@router.post("/{vault_id}/exports", response_model=ExportResponse, status_code=202)
async def create_export(
    vault_id: uuid.UUID,
    payload: ExportCreate,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    actor = await resolve_actor(current_user)
    vault = await get_vault_or_404(db, vault_id)
    await require_vault_role(db, vault=vault, actor=actor, minimum="curator")
    export_stats = await db.execute(
        select(
            func.count(MediaItem.id),
            func.coalesce(func.sum(MediaItem.size_bytes), 0),
        ).where(
            MediaItem.vault_id == vault_id,
            MediaItem.id.in_(payload.media_item_ids),
            MediaItem.processing_status == "ready",
            MediaItem.soft_deleted_at.is_(None),
        )
    )
    count, selected_bytes = export_stats.one()
    count = int(count or 0)
    if count != len(set(payload.media_item_ids)):
        raise HTTPException(
            status_code=404, detail="One or more media items are missing"
        )
    if payload.preset == "original" and int(selected_bytes or 0) >= 5 * 1024**4:
        raise HTTPException(
            status_code=413,
            detail="Select less than five TiB per original ZIP export",
        )
    export = MediaVaultExport(
        vault_id=vault_id,
        requested_by=actor.auth_id,
        preset=payload.preset,
        media_item_ids=[str(item_id) for item_id in payload.media_item_ids],
        expires_at=utc_now() + timedelta(hours=settings.MEDIA_VAULT_EXPORT_TTL_HOURS),
    )
    db.add(export)
    await db.commit()
    await db.refresh(export)
    pool = await _get_redis_pool()
    await pool.enqueue_job(
        "task_build_vault_export",
        str(export.id),
        _queue_name="arq:media",
    )
    return export


@router.get("/{vault_id}/exports", response_model=list[ExportResponse])
async def list_exports(
    vault_id: uuid.UUID,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    actor = await resolve_actor(current_user)
    vault = await get_vault_or_404(db, vault_id)
    await require_vault_role(db, vault=vault, actor=actor, minimum="curator")
    rows = await db.execute(
        select(MediaVaultExport)
        .where(MediaVaultExport.vault_id == vault_id)
        .order_by(desc(MediaVaultExport.created_at))
        .limit(100)
    )
    return list(rows.scalars().all())


@router.post(
    "/{vault_id}/exports/{export_id}/download",
    response_model=DownloadAuthorizationResponse,
)
async def authorize_export_download(
    vault_id: uuid.UUID,
    export_id: uuid.UUID,
    request: Request,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    actor = await resolve_actor(current_user)
    vault = await get_vault_or_404(db, vault_id)
    await require_vault_role(db, vault=vault, actor=actor, minimum="curator")
    export = await db.scalar(
        select(MediaVaultExport).where(
            MediaVaultExport.id == export_id,
            MediaVaultExport.vault_id == vault_id,
        )
    )
    if not export or export.status != "ready" or not export.object_key:
        raise HTTPException(status_code=409, detail="Export is not ready")
    if export.expires_at < utc_now():
        raise HTTPException(status_code=410, detail="Export has expired")
    ttl = min(
        settings.MEDIA_VAULT_SIGNED_URL_TTL_SECONDS,
        max(60, int((export.expires_at - utc_now()).total_seconds())),
    )
    url, delivery_method = await storage_service.generate_private_download_url(
        file_key=export.object_key,
        expiration=ttl,
        download_name=f"{vault.capture_date.isoformat()}-{vault.title}.zip",
    )
    ip, user_agent = _request_metadata(request)
    transfer = MediaTransferLog(
        vault_id=vault_id,
        export_id=export.id,
        object_key=export.object_key,
        actor_id=actor.auth_id,
        actor_member_id=actor.member_id,
        transfer_type="export",
        direction="download",
        delivery_method=delivery_method,
        bytes_authorized=export.size_bytes,
        ip_address=ip,
        user_agent=user_agent,
    )
    db.add(transfer)
    await db.commit()
    await db.refresh(transfer)
    return DownloadAuthorizationResponse(
        transfer_id=transfer.id,
        url=url,
        expires_in_seconds=ttl,
        bytes_authorized=export.size_bytes,
        filename=f"{vault.capture_date.isoformat()}-{vault.title}.zip",
    )


@router.get("/admin/bandwidth", response_model=BandwidthSummary)
async def bandwidth_summary(
    months: int = Query(default=12, ge=1, le=36),
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    start = utc_now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    start = (start - timedelta(days=31 * (months - 1))).replace(day=1)
    month_expr = func.date_trunc("month", MediaTransferLog.created_at)
    reconciled_sources = ("s3_access_log",)
    is_reconciled = MediaTransferLog.measurement_source.in_(reconciled_sources)
    transfer_rows = await db.execute(
        select(
            month_expr.label("month"),
            func.coalesce(
                func.sum(
                    case(
                        (
                            MediaTransferLog.direction == "upload",
                            MediaTransferLog.bytes_transferred,
                        ),
                        else_=0,
                    )
                ),
                0,
            ),
            func.coalesce(
                func.sum(
                    case(
                        (
                            MediaTransferLog.direction == "download",
                            MediaTransferLog.bytes_authorized,
                        ),
                        else_=0,
                    )
                ),
                0,
            ),
            func.coalesce(
                func.sum(
                    case(
                        (
                            MediaTransferLog.direction == "download",
                            MediaTransferLog.bytes_transferred,
                        ),
                        else_=0,
                    )
                ),
                0,
            ),
            func.coalesce(
                func.sum(
                    case(
                        (
                            (MediaTransferLog.direction == "download") & ~is_reconciled,
                            MediaTransferLog.bytes_authorized,
                        ),
                        else_=0,
                    )
                ),
                0,
            ),
        )
        .where(
            MediaTransferLog.created_at >= start,
        )
        .group_by(month_expr)
        .order_by(month_expr)
    )
    access_month_expr = func.date_trunc("month", MediaAccessLogEvent.occurred_at)
    access_rows = await db.execute(
        select(
            access_month_expr.label("month"),
            func.coalesce(func.sum(MediaAccessLogEvent.bytes_sent), 0),
        )
        .where(
            MediaAccessLogEvent.occurred_at >= start,
            MediaAccessLogEvent.provider == "s3",
            MediaAccessLogEvent.match_status == "matched",
        )
        .group_by(access_month_expr)
        .order_by(access_month_expr)
    )
    buckets_by_month: dict[str, dict[str, int | str]] = {}
    for month, upload, authorized, completed, pending in transfer_rows.all():
        month_key = month.astimezone(timezone.utc).strftime("%Y-%m")
        buckets_by_month[month_key] = {
            "month": month_key,
            "upload_bytes": int(upload),
            "download_authorized_bytes": int(authorized),
            "download_completed_bytes": int(completed),
            "download_reconciled_bytes": 0,
            "download_pending_estimate_bytes": int(pending),
            "download_effective_bytes": int(pending),
        }
    for month, reconciled in access_rows.all():
        month_key = month.astimezone(timezone.utc).strftime("%Y-%m")
        bucket = buckets_by_month.setdefault(
            month_key,
            {
                "month": month_key,
                "upload_bytes": 0,
                "download_authorized_bytes": 0,
                "download_completed_bytes": 0,
                "download_reconciled_bytes": 0,
                "download_pending_estimate_bytes": 0,
                "download_effective_bytes": 0,
            },
        )
        bucket["download_reconciled_bytes"] = int(reconciled)
        bucket["download_effective_bytes"] = int(
            bucket["download_pending_estimate_bytes"]
        ) + int(reconciled)
    buckets = [buckets_by_month[key] for key in sorted(buckets_by_month)]
    current_key = utc_now().strftime("%Y-%m")
    current_effective = next(
        (
            bucket["download_effective_bytes"]
            for bucket in buckets
            if bucket["month"] == current_key
        ),
        0,
    )
    last_processed_at = await db.scalar(
        select(func.max(MediaAccessLogObject.processed_at)).where(
            MediaAccessLogObject.provider == "s3",
            MediaAccessLogObject.status == "completed",
        )
    )
    allowance = 100 * 1024**3
    return BandwidthSummary(
        months=buckets,
        current_month_download_bytes=current_effective,
        allowance_remaining_bytes=max(0, allowance - current_effective),
        reconciliation_enabled=bool(settings.MEDIA_VAULT_ACCESS_LOG_BUCKET.strip()),
        reconciliation_last_processed_at=last_processed_at,
        measurement_note=(
            "Effective download usage uses actual S3 bytes for reconciled requests, "
            "and authorized bytes while reconciliation is pending or outside log "
            "coverage. S3 access logs are best-effort and can be delayed or omitted. "
            "AWS's 100 GB allowance is shared account-wide, so AWS Billing remains "
            "authoritative."
        ),
    )


@router.post(
    "/{vault_id}/items/{item_id}/takedown",
    response_model=TakedownResponse,
    status_code=201,
)
async def request_takedown(
    vault_id: uuid.UUID,
    item_id: uuid.UUID,
    payload: TakedownCreate,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    actor = await resolve_actor(current_user)
    vault = await get_vault_or_404(db, vault_id)
    role = await require_vault_role(db, vault=vault, actor=actor, minimum="contributor")
    item = await db.scalar(
        select(MediaItem).where(MediaItem.id == item_id, MediaItem.vault_id == vault_id)
    )
    if not item:
        raise HTTPException(status_code=404, detail="Vault media not found")
    if role == "contributor" and item.uploaded_by != actor.auth_id:
        raise HTTPException(status_code=403, detail="Takedown access denied")
    item.consent_status = "takedown"
    if item.published_media_id:
        published = await db.get(MediaItem, item.published_media_id)
        if published:
            album_items = await db.execute(
                select(AlbumItem).where(AlbumItem.media_item_id == published.id)
            )
            for album_item in album_items.scalars().all():
                await db.delete(album_item)
            await storage_service.delete_media(
                published.file_url,
                bucket_type=BucketType.PUBLIC,
                strict=True,
            )
            published.soft_deleted_at = utc_now()
        item.metadata_info = {
            **(item.metadata_info or {}),
            "takedown_published_media_id": str(item.published_media_id),
        }
        item.published_media_id = None
        item.published_at = None
        item.review_status = "rejected"
    takedown = MediaTakedownRequest(
        media_item_id=item.id,
        requested_by=actor.auth_id,
        reason=payload.reason,
    )
    db.add(takedown)
    await db.commit()
    await db.refresh(takedown)
    return takedown


@router.patch("/{vault_id}/takedowns/{takedown_id}", response_model=TakedownResponse)
async def resolve_takedown(
    vault_id: uuid.UUID,
    takedown_id: uuid.UUID,
    payload: TakedownResolve,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    actor = await resolve_actor(current_user)
    vault = await get_vault_or_404(db, vault_id)
    await require_vault_role(db, vault=vault, actor=actor, minimum="admin")
    takedown = await db.scalar(
        select(MediaTakedownRequest)
        .join(MediaItem)
        .where(
            MediaTakedownRequest.id == takedown_id,
            MediaItem.vault_id == vault_id,
        )
    )
    if not takedown:
        raise HTTPException(status_code=404, detail="Takedown request not found")
    takedown.status = payload.status
    takedown.resolution_notes = payload.resolution_notes
    takedown.resolved_by = actor.auth_id
    takedown.resolved_at = utc_now()
    await db.commit()
    await db.refresh(takedown)
    return takedown
