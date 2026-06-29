"""Internal media object APIs for service-to-service storage operations."""

from __future__ import annotations

import base64
import binascii
import mimetypes
import re
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status

from libs.auth.dependencies import require_service_role
from libs.auth.models import AuthUser
from services.media_service.schemas import (
    InternalDirectUploadCreateRequest,
    InternalDirectUploadCreateResponse,
    InternalObjectMetadataResponse,
    InternalObjectSignRequest,
    InternalObjectSignResponse,
    InternalObjectUploadRequest,
    InternalObjectUploadResponse,
    InternalObjectVerifyRequest,
)
from services.media_service.services.storage import (
    BucketType,
    get_bucket_for_purpose,
    storage_service,
)

router = APIRouter(prefix="/internal/media", tags=["media-internal"])

_MAX_INTERNAL_UPLOAD_BYTES = 20 * 1024 * 1024
_PURPOSE_PREFIX = {
    "strokelab_original": "strokelab/original",
    "strokelab_annotated": "strokelab/annotated",
    "strokelab_evidence": "strokelab/evidence",
    "strokelab_share": "strokelab/share",
}


def _safe_segment(value: str | None, fallback: str) -> str:
    raw = (value or "").strip()
    safe = re.sub(r"[^A-Za-z0-9._=-]+", "_", raw).strip("._-")
    return safe[:160] or fallback


def _suffix(filename: str, content_type: str) -> str:
    raw = Path(filename or "").suffix.lower()
    if raw and 1 < len(raw) <= 10:
        return raw
    guessed = mimetypes.guess_extension(content_type or "") or ".bin"
    return ".mov" if guessed == ".qt" else guessed[:10]


def _object_key(purpose: str, linked_id: str | None, filename: str, mime: str) -> str:
    prefix = _PURPOSE_PREFIX.get(purpose) or _safe_segment(purpose, "general")
    link = _safe_segment(linked_id, "unlinked")
    return f"{prefix}/{link}/{uuid.uuid4().hex}{_suffix(filename, mime)}"


def _bucket_type(raw: str) -> BucketType:
    try:
        return BucketType(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid bucket_type") from exc


@router.post(
    "/direct-uploads",
    response_model=InternalDirectUploadCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_direct_upload(
    req: InternalDirectUploadCreateRequest,
    _current_user: AuthUser = Depends(require_service_role),
) -> InternalDirectUploadCreateResponse:
    """Issue a media-owned presigned PUT URL for browser direct uploads."""
    bucket_type = get_bucket_for_purpose(req.purpose)
    object_key = _object_key(req.purpose, req.linked_id, req.filename, req.content_type)
    try:
        upload_url = await storage_service.generate_presigned_url(
            object_key,
            bucket_type=bucket_type,
            expiration=req.expires_in,
            operation="put_object",
            content_type=req.content_type or "application/octet-stream",
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=str(exc),
        ) from exc
    return InternalDirectUploadCreateResponse(
        object_key=object_key,
        bucket_type=bucket_type.value,
        upload_url=upload_url,
        headers={"Content-Type": req.content_type or "application/octet-stream"},
        expires_in=req.expires_in,
    )


@router.post("/objects/verify", response_model=InternalObjectMetadataResponse)
async def verify_object(
    req: InternalObjectVerifyRequest,
    _current_user: AuthUser = Depends(require_service_role),
) -> InternalObjectMetadataResponse:
    """Return object metadata after a direct upload completes."""
    try:
        meta = await storage_service.head_object(
            req.object_key, bucket_type=_bucket_type(req.bucket_type)
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Object not found") from exc
    return InternalObjectMetadataResponse(**meta)


@router.post("/objects/sign", response_model=InternalObjectSignResponse)
async def sign_object(
    req: InternalObjectSignRequest,
    _current_user: AuthUser = Depends(require_service_role),
) -> InternalObjectSignResponse:
    """Return a short-lived read URL for a private media object."""
    try:
        url = await storage_service.generate_presigned_url(
            req.object_key,
            bucket_type=_bucket_type(req.bucket_type),
            expiration=req.expires_in,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=str(exc),
        ) from exc
    return InternalObjectSignResponse(url=url, expires_in=req.expires_in)


@router.post(
    "/objects/upload",
    response_model=InternalObjectUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_object(
    req: InternalObjectUploadRequest,
    _current_user: AuthUser = Depends(require_service_role),
) -> InternalObjectUploadResponse:
    """Upload small generated media artifacts owned by another service."""
    try:
        data = base64.b64decode(req.data_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Invalid base64 data") from exc
    if not data:
        raise HTTPException(status_code=400, detail="Empty upload")
    if len(data) > _MAX_INTERNAL_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Internal object is too large")

    bucket_type = get_bucket_for_purpose(req.purpose)
    object_key = _object_key(req.purpose, req.linked_id, req.filename, req.content_type)
    url, _ = await storage_service.upload_media(
        bucket_type=bucket_type,
        file_data=data,
        filename=object_key,
        content_type=req.content_type or "application/octet-stream",
        preserve_filename=True,
        generate_thumbnail=False,
    )
    return InternalObjectUploadResponse(
        object_key=object_key,
        bucket_type=bucket_type.value,
        url=url,
    )


@router.post("/objects/delete")
async def delete_object(
    req: InternalObjectVerifyRequest,
    _current_user: AuthUser = Depends(require_service_role),
) -> dict:
    """Best-effort object deletion by key."""
    await storage_service.delete_media(
        req.object_key,
        bucket_type=_bucket_type(req.bucket_type),
        is_key=True,
    )
    return {"deleted": True}
