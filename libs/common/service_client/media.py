"""High-level helpers for internal media-service object operations."""

from __future__ import annotations

import base64
from typing import Any

from libs.common.config import get_settings

from .core import internal_post


async def create_media_direct_upload(
    *,
    purpose: str,
    filename: str,
    content_type: str,
    size_bytes: int,
    calling_service: str,
    linked_id: str | None = None,
    expires_in: int = 900,
) -> dict[str, Any]:
    settings = get_settings()
    resp = await internal_post(
        service_url=settings.MEDIA_SERVICE_URL,
        path="/internal/media/direct-uploads",
        calling_service=calling_service,
        json={
            "purpose": purpose,
            "filename": filename,
            "content_type": content_type,
            "size_bytes": size_bytes,
            "linked_id": linked_id,
            "expires_in": expires_in,
        },
    )
    resp.raise_for_status()
    return resp.json()


async def verify_media_object(
    *,
    object_key: str,
    calling_service: str,
    bucket_type: str = "private",
) -> dict[str, Any]:
    settings = get_settings()
    resp = await internal_post(
        service_url=settings.MEDIA_SERVICE_URL,
        path="/internal/media/objects/verify",
        calling_service=calling_service,
        json={"object_key": object_key, "bucket_type": bucket_type},
    )
    resp.raise_for_status()
    return resp.json()


async def sign_media_object(
    *,
    object_key: str,
    calling_service: str,
    bucket_type: str = "private",
    expires_in: int = 3600,
) -> str:
    settings = get_settings()
    resp = await internal_post(
        service_url=settings.MEDIA_SERVICE_URL,
        path="/internal/media/objects/sign",
        calling_service=calling_service,
        json={
            "object_key": object_key,
            "bucket_type": bucket_type,
            "expires_in": expires_in,
        },
    )
    resp.raise_for_status()
    return str(resp.json()["url"])


async def upload_media_object(
    *,
    purpose: str,
    filename: str,
    content_type: str,
    data: bytes,
    calling_service: str,
    linked_id: str | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    resp = await internal_post(
        service_url=settings.MEDIA_SERVICE_URL,
        path="/internal/media/objects/upload",
        calling_service=calling_service,
        json={
            "purpose": purpose,
            "filename": filename,
            "content_type": content_type,
            "data_base64": base64.b64encode(data).decode("ascii"),
            "linked_id": linked_id,
        },
        timeout=30.0,
    )
    resp.raise_for_status()
    return resp.json()


async def delete_media_object(
    *,
    object_key: str,
    calling_service: str,
    bucket_type: str = "private",
) -> None:
    settings = get_settings()
    resp = await internal_post(
        service_url=settings.MEDIA_SERVICE_URL,
        path="/internal/media/objects/delete",
        calling_service=calling_service,
        json={"object_key": object_key, "bucket_type": bucket_type},
    )
    resp.raise_for_status()
