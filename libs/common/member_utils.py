"""Member lookup utilities for cross-service member data resolution.

Uses HTTP calls to the members service instead of direct DB queries to maintain
proper service boundaries in the microservices architecture.
"""

import uuid
from typing import Optional

import httpx

from libs.common.config import get_settings
from libs.common.logging import get_logger
from libs.common.service_client import get_members_bulk as get_members_bulk_internal

settings = get_settings()
logger = get_logger(__name__)


class MemberBasicInfo:
    """Basic member info returned from bulk lookup."""

    __slots__ = ("id", "first_name", "last_name", "email", "profile_photo_url")

    def __init__(
        self,
        id: str,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        email: Optional[str] = None,
        profile_photo_url: Optional[str] = None,
    ):
        self.id = id
        self.first_name = first_name
        self.last_name = last_name
        self.email = email
        self.profile_photo_url = profile_photo_url

    @property
    def full_name(self) -> str:
        return f"{self.first_name or ''} {self.last_name or ''}".strip()


async def resolve_member_basic(
    member_id: uuid.UUID | str,
) -> Optional[MemberBasicInfo]:
    """
    Resolve a single member_id to basic info via HTTP call to members service.

    Args:
        member_id: The UUID of the member

    Returns:
        MemberBasicInfo or None if not found
    """
    if not member_id:
        return None

    result = await resolve_members_basic([member_id])
    return result.get(str(member_id))


async def resolve_members_basic(
    member_ids: list[uuid.UUID | str],
) -> dict[str, MemberBasicInfo]:
    """
    Resolve multiple member_ids to basic info via the internal members service
    endpoint. This is the primary path for backend-to-backend enrichment.

    Returns id, first_name, last_name, email. Does NOT include profile_photo_url.
    Use resolve_members_with_photos() if you need photos.

    Args:
        member_ids: List of member UUIDs to resolve

    Returns:
        Dictionary mapping member_id (string) -> MemberBasicInfo
    """
    valid_ids = [mid for mid in member_ids if mid is not None]
    if not valid_ids:
        return {}

    try:
        data = await get_members_bulk_internal(
            [str(mid) for mid in valid_ids], calling_service="member_utils"
        )
        result: dict[str, MemberBasicInfo] = {}
        for info in data:
            member_id = str(info.get("id"))
            result[member_id] = MemberBasicInfo(
                id=member_id,
                first_name=info.get("first_name"),
                last_name=info.get("last_name"),
                email=info.get("email"),
                profile_photo_url=None,
            )
        return result
    except Exception as e:
        logger.warning("Failed to resolve member info via internal bulk lookup: %r", e)
        return {}


async def resolve_members_with_photos(
    member_ids: list[uuid.UUID | str],
) -> dict[str, MemberBasicInfo]:
    """
    Resolve multiple member_ids to info INCLUDING profile_photo_url.

    Uses the public /members/bulk-basic endpoint which returns photo URLs.
    Falls back to resolve_members_basic() (without photos) on timeout or
    HTTP errors.

    Use this only where profile_photo_url is actually needed (e.g. spotlight).
    For name/email enrichment, prefer resolve_members_basic().

    Args:
        member_ids: List of member UUIDs to resolve

    Returns:
        Dictionary mapping member_id (string) -> MemberBasicInfo
    """
    valid_ids = [mid for mid in member_ids if mid is not None]
    if not valid_ids:
        return {}

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(
                f"{settings.MEMBERS_SERVICE_URL}/members/bulk-basic",
                json=[str(mid) for mid in valid_ids],
            )
            response.raise_for_status()
            data = response.json()

            result: dict[str, MemberBasicInfo] = {}
            for key, info in data.items():
                result[str(key)] = MemberBasicInfo(
                    id=str(key),
                    first_name=info.get("first_name"),
                    last_name=info.get("last_name"),
                    email=info.get("email"),
                    profile_photo_url=info.get("profile_photo_url"),
                )
            return result
    except (httpx.TimeoutException, httpx.HTTPStatusError) as e:
        logger.warning(
            "Public bulk-basic lookup failed (%s), falling back to internal endpoint",
            type(e).__name__,
        )
        return await resolve_members_basic(valid_ids)
