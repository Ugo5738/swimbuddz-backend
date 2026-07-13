"""
Background task for scheduled content post publishing.

Publishes content posts whose scheduled_for time has arrived.
Runs every hour via ARQ cron to catch any posts due for publishing.
"""

from uuid import UUID

from libs.common.config import get_settings
from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from libs.common.service_client import internal_get
from libs.common.session_access import active_paid_tiers
from libs.db.session import get_async_db
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.communications_service.models import (
    ContentPost,
    ContentPostEmailLog,
    NotificationPreferences,
)
from services.communications_service.templates.content import (
    send_content_post_published_email,
)

logger = get_logger(__name__)


def _content_tier(value: str | None) -> str:
    tier = str(value or "community").lower()
    if tier in {"club", "academy"}:
        return tier
    return "community"


def _member_can_read_post(member: dict, post: ContentPost) -> bool:
    tier = _content_tier(post.tier_access)
    if tier == "community":
        return True

    paid_tiers = active_paid_tiers(member)
    if tier == "club":
        return "club" in paid_tiers
    return "academy" in paid_tiers


async def _get_content_preferences_by_auth(
    db: AsyncSession,
    members: list[dict],
) -> dict[str, NotificationPreferences]:
    auth_ids = [m["auth_id"] for m in members if m.get("auth_id")]
    if not auth_ids:
        return {}
    prefs_result = await db.execute(
        select(NotificationPreferences).where(
            NotificationPreferences.member_auth_id.in_(auth_ids)
        )
    )
    return {p.member_auth_id: p for p in prefs_result.scalars().all()}


async def _get_article_email_logs(
    db: AsyncSession,
    post_id: UUID,
) -> dict[str, ContentPostEmailLog]:
    result = await db.execute(
        select(ContentPostEmailLog).where(
            ContentPostEmailLog.post_id == post_id,
            ContentPostEmailLog.channel == "email",
        )
    )
    return {str(log.member_id): log for log in result.scalars().all()}


async def send_content_post_publish_emails(
    db: AsyncSession,
    post: ContentPost,
) -> int:
    """Send article-published emails once per member when enabled for the post."""
    if not post.email_on_publish:
        return 0

    settings = get_settings()
    members_resp = await internal_get(
        service_url=settings.MEMBERS_SERVICE_URL,
        path="/internal/members/active",
        calling_service="communications",
    )
    if members_resp.status_code != 200:
        logger.error("Failed to get active members for content post email")
        return 0

    members = members_resp.json()
    prefs_map = await _get_content_preferences_by_auth(db, members)
    logs_by_member_id = await _get_article_email_logs(db, post.id)

    sent_count = 0
    for member in members:
        member_id = member.get("id")
        member_email = member.get("email")
        if not member_id or not member_email:
            continue

        pref = prefs_map.get(member.get("auth_id"))
        if pref and pref.weekly_digest is False:
            continue
        if not _member_can_read_post(member, post):
            continue

        existing_log = logs_by_member_id.get(str(member_id))
        if existing_log and existing_log.delivery_status == "sent":
            continue

        log = existing_log or ContentPostEmailLog(
            post_id=post.id,
            member_id=UUID(str(member_id)),
            channel="email",
        )
        try:
            success = await send_content_post_published_email(
                to_email=member_email,
                member_name=member.get("first_name") or "there",
                post_id=str(post.id),
                title=post.title,
                summary=post.summary,
                category=post.category,
            )
        except Exception as exc:
            logger.error(
                "Failed to send content post email post=%s member=%s: %s",
                post.id,
                member_id,
                exc,
            )
            success = False
            log.error_message = str(exc)

        now = utc_now()
        if success:
            log.sent_at = now
            log.delivery_status = "sent"
            log.error_message = None
            sent_count += 1
        else:
            log.delivery_status = "failed"
            if not log.error_message:
                log.error_message = "Email provider returned failure"

        if existing_log is None:
            db.add(log)

    await db.commit()
    logger.info(
        "Sent content post email post=%s to %d member(s)",
        post.id,
        sent_count,
    )
    return sent_count


async def publish_scheduled_content() -> None:
    """
    Find and publish all content posts whose scheduled_for <= now
    and are not yet published.
    """
    now = utc_now()

    async for db in get_async_db():
        query = select(ContentPost).where(
            ContentPost.scheduled_for.isnot(None),
            ContentPost.scheduled_for <= now,
            ContentPost.is_published.is_(False),
        )
        result = await db.execute(query)
        posts = result.scalars().all()

        if not posts:
            logger.info("No scheduled content posts due for publishing.")
            return

        for post in posts:
            scheduled_for = post.scheduled_for
            post.is_published = True
            post.published_at = now
            post.scheduled_for = None
            logger.info(
                "Auto-published content post: %s (scheduled_for=%s)",
                post.title,
                scheduled_for,
            )

        await db.commit()
        for post in posts:
            await db.refresh(post)
            await send_content_post_publish_emails(db, post)
        logger.info("Published %d scheduled content post(s).", len(posts))
