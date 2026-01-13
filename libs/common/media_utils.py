"""Media URL resolution utilities for cross-service media lookups.

Uses HTTP calls to the media service instead of direct DB queries to maintain
proper service boundaries in the microservices architecture.
"""

import os
import uuid
from typing import Optional

import httpx
from libs.common.logging import get_logger

# Media service URL - uses internal Docker network in production
MEDIA_SERVICE_URL = os.getenv("MEDIA_SERVICE_URL", "http://media-service:8008")
logger = get_logger(__name__)


async def resolve_media_url(media_id: Optional[uuid.UUID]) -> Optional[str]:
    """
    Resolve a media_id to its file_url via HTTP call to media service.

    Args:
        media_id: The UUID of the media item

    Returns:
        The file_url of the media item, or None if not found
    """
    if not media_id:
        return None

    url_map = await resolve_media_urls([media_id])
    # Normalize lookup to string for consistency across callers
    return url_map.get(str(media_id))


async def resolve_media_urls(
    media_ids: list[Optional[uuid.UUID | str]],
) -> dict[uuid.UUID | str, str]:
    """
    Resolve multiple media_ids to their file_urls via HTTP call to media service.

    Args:
        media_ids: List of media UUIDs to resolve

    Returns:
        Dictionary mapping media_id (string and UUID keys) to file_url
    """
    valid_ids = [mid for mid in media_ids if mid is not None]
    if not valid_ids:
        return {}

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(
                f"{MEDIA_SERVICE_URL}/api/v1/media/urls",
                json=[str(mid) for mid in valid_ids],
            )
            response.raise_for_status()
            data = response.json()

            # Return map that works with either UUID or string lookups
            url_map: dict[uuid.UUID | str, str] = {}
            for key, url in data.items():
                key_str = str(key)
                url_map[key_str] = url
                try:
                    url_map[uuid.UUID(key_str)] = url
                except (ValueError, TypeError):
                    continue
            return url_map
    except Exception as e:
        # Log error but don't break the service
        logger.warning(f"Failed to resolve media URLs: {e}")
        return {}


def enrich_with_media_url(
    obj: dict,
    media_url_map: dict[uuid.UUID | str, str],
    media_id_field: str = "profile_photo_media_id",
    url_field: str = "profile_photo_url",
) -> dict:
    """
    Enrich an object dict with a resolved media URL.

    Args:
        obj: The object dictionary to enrich
        media_url_map: Map of media_id to file_url
        media_id_field: The field name containing the media_id
        url_field: The field name to add for the URL

    Returns:
        The enriched object dict
    """
    media_id = obj.get(media_id_field)
    if media_id and media_id in media_url_map:
        obj[url_field] = media_url_map[media_id]
    else:
        obj[url_field] = None
    return obj
