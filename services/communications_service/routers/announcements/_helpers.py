"""Helpers for announcements sub-routers."""

"""Communications announcements router: announcements, read tracking, comments."""

from datetime import datetime, timedelta
from typing import Optional, Set

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.models import AuthUser
from libs.common.config import get_settings
from libs.common.logging import get_logger
from libs.common.datetime_utils import utc_now
from services.communications_service.models import (
    Announcement,
    AnnouncementAudience,
    AnnouncementCategory,
    AnnouncementStatus,
    NotificationPreferences,
)
from services.communications_service.templates.messaging import send_message_email

settings = get_settings()
logger = get_logger(__name__)


def _is_admin(user: Optional[AuthUser]) -> bool:
    if not user:
        return False
    is_service_role = user.role == "service_role"
    has_admin_role = user.has_role("admin")
    is_whitelisted_email = user.email is not None and user.email in (
        settings.ADMIN_EMAILS or []
    )
    return is_service_role or has_admin_role or is_whitelisted_email


def _default_expiry(category: AnnouncementCategory) -> Optional[datetime]:
    now = utc_now()
    if category in (
        AnnouncementCategory.RAIN_UPDATE,
        AnnouncementCategory.SCHEDULE_CHANGE,
    ):
        return now + timedelta(hours=24)
    return None


def _default_notification_flags(category: AnnouncementCategory) -> tuple[bool, bool]:
    if category in (
        AnnouncementCategory.RAIN_UPDATE,
        AnnouncementCategory.SCHEDULE_CHANGE,
    ):
        return True, True  # email + push
    if category in (AnnouncementCategory.EVENT, AnnouncementCategory.COMPETITION):
        return True, False  # email by default
    return True, False


async def _get_allowed_audiences(authorization: Optional[str]) -> Set[str]:
    # Guests only see community announcements.
    if not authorization:
        return {"community"}

    url = f"{settings.MEMBERS_SERVICE_URL}/members/me"
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(url, headers={"Authorization": authorization})
        if response.status_code != 200:
            return {"community"}
        data = response.json()
    except httpx.RequestError:
        return {"community"}

    membership = data.get("membership") or {}
    active_tiers = membership.get("active_tiers") or []
    primary_tier = membership.get("primary_tier")
    tiers = {str(t).lower() for t in active_tiers if t}
    if primary_tier:
        tiers.add(str(primary_tier).lower())

    # Higher tiers inherit lower tier announcements.
    if "academy" in tiers:
        tiers.add("club")
    tiers.add("community")
    return tiers


def _pref_allows_email(
    category: AnnouncementCategory, pref: Optional[NotificationPreferences]
) -> bool:
    if not pref:
        return True
    if category == AnnouncementCategory.ACADEMY_UPDATE:
        return pref.email_academy_updates
    return pref.email_announcements


async def _fetch_members_for_audience(audience: AnnouncementAudience) -> list[dict]:
    url = f"{settings.MEMBERS_SERVICE_URL}/members/"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(url, params={"skip": 0, "limit": 1000})
        if response.status_code != 200:
            logger.warning("Failed to fetch members list for announcement delivery.")
            return []
        members = response.json()
    except httpx.RequestError as exc:
        logger.warning("Failed to reach members service: %s", exc)
        return []

    if audience == AnnouncementAudience.COMMUNITY:
        return members

    filtered = []
    async with httpx.AsyncClient(timeout=10) as client:
        for member in members:
            auth_id = member.get("auth_id")
            if not auth_id:
                continue
            try:
                detail_resp = await client.get(
                    f"{settings.MEMBERS_SERVICE_URL}/members/by-auth/{auth_id}"
                )
                if detail_resp.status_code != 200:
                    continue
                detail = detail_resp.json()
            except httpx.RequestError:
                continue

            membership = detail.get("membership") or {}
            active_tiers = membership.get("active_tiers") or []
            primary_tier = membership.get("primary_tier")
            tiers = {str(t).lower() for t in active_tiers if t}
            if primary_tier:
                tiers.add(str(primary_tier).lower())
            if "academy" in tiers:
                tiers.add("club")

            if audience.value in tiers:
                filtered.append(member)

    return filtered


async def _send_announcement_emails(
    announcement: Announcement,
    db: AsyncSession,
) -> None:
    if (
        announcement.status != AnnouncementStatus.PUBLISHED
        or not announcement.notify_email
    ):
        return

    members = await _fetch_members_for_audience(announcement.audience)
    if not members:
        return

    member_ids = [m.get("id") for m in members if m.get("id")]
    pref_map: dict[str, NotificationPreferences] = {}
    if member_ids:
        prefs_result = await db.execute(
            select(NotificationPreferences).where(
                NotificationPreferences.member_id.in_(member_ids)
            )
        )
        pref_map = {str(p.member_id): p for p in prefs_result.scalars().all()}

    subject = f"SwimBuddz Update: {announcement.title}"
    body = announcement.body
    if announcement.summary:
        body = f"{announcement.summary}\n\n{announcement.body}"

    sent_count = 0
    for member in members:
        email = member.get("email")
        member_id = str(member.get("id")) if member.get("id") else None
        if not email:
            continue
        pref = pref_map.get(member_id) if member_id else None
        if not _pref_allows_email(announcement.category, pref):
            continue
        success = await send_message_email(
            to_email=email,
            subject=subject,
            body=body,
        )
        if success:
            sent_count += 1

    if sent_count:
        logger.info("Sent announcement email to %s recipients", sent_count)
