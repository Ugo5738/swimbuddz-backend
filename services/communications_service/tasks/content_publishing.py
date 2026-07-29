"""Scheduled article publishing and durable publish-email delivery."""

from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import and_, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.config import get_settings
from libs.common.datetime_utils import utc_now
from libs.common.emails.core import EmailDeliveryUnknownError
from libs.common.logging import get_logger
from libs.common.media_utils import resolve_media_url
from libs.common.service_client import internal_get
from libs.common.session_access import active_paid_tiers
from libs.db.session import get_async_db
from services.communications_service.models import (
    ContentPost,
    ContentPostEmailLog,
    NotificationPreferences,
)
from services.communications_service.templates.content import (
    send_content_post_published_email,
)

logger = get_logger(__name__)

MAX_AUTOMATIC_EMAIL_ATTEMPTS = 5
INTERRUPTED_SEND_TIMEOUT = timedelta(minutes=30)
DISPATCH_RETRY_DELAY = timedelta(minutes=5)


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


async def _record_dispatch_error(
    db: AsyncSession,
    post: ContentPost,
    message: str,
) -> None:
    now = utc_now()
    await db.execute(
        update(ContentPost)
        .where(ContentPost.id == post.id)
        .values(
            email_dispatch_last_attempt_at=now,
            email_dispatch_last_error=message[:2000],
            updated_at=now,
        )
    )
    await db.commit()


async def _snapshot_article_recipients(
    db: AsyncSession,
    post: ContentPost,
) -> bool:
    """Persist the eligible publish-time audience before sending any email."""
    if post.email_recipient_snapshot_at is not None:
        return True

    settings = get_settings()
    try:
        members_resp = await internal_get(
            service_url=settings.MEMBERS_SERVICE_URL,
            path="/internal/members/active",
            calling_service="communications",
        )
        if members_resp.status_code != 200:
            await _record_dispatch_error(
                db,
                post,
                f"Active member lookup failed with status {members_resp.status_code}",
            )
            return False
        members = members_resp.json()
        if not isinstance(members, list):
            raise ValueError("Active member response must be a list")
    except Exception as exc:
        logger.error(
            "Failed to snapshot recipients for content post %s: %s",
            post.id,
            exc,
        )
        await _record_dispatch_error(db, post, f"Active member lookup failed: {exc}")
        return False

    prefs_map = await _get_content_preferences_by_auth(db, members)
    now = utc_now()
    recipients: list[dict] = []
    for member in members:
        member_id = member.get("id")
        member_email = member.get("email")
        if not member_id or not member_email:
            continue
        pref = prefs_map.get(member.get("auth_id"))
        if pref and pref.email_content_updates is False:
            continue
        if not _member_can_read_post(member, post):
            continue
        try:
            parsed_member_id = UUID(str(member_id))
        except (TypeError, ValueError):
            logger.warning(
                "Skipping invalid member id in content recipient snapshot: %s",
                member_id,
            )
            continue
        recipients.append(
            {
                "id": uuid4(),
                "post_id": post.id,
                "member_id": parsed_member_id,
                "recipient_email": str(member_email),
                "recipient_name": str(member.get("first_name") or "there"),
                "channel": "email",
                "delivery_status": "pending",
                "attempt_count": 0,
                "created_at": now,
                "updated_at": now,
            }
        )

    locked_result = await db.execute(
        select(ContentPost).where(ContentPost.id == post.id).with_for_update()
    )
    locked_post = locked_result.scalar_one()
    if locked_post.email_recipient_snapshot_at is not None:
        await db.commit()
        return True

    if recipients:
        await db.execute(
            insert(ContentPostEmailLog)
            .values(recipients)
            .on_conflict_do_nothing(index_elements=["post_id", "member_id", "channel"])
        )
    locked_post.email_recipient_snapshot_at = now
    locked_post.email_dispatch_last_attempt_at = now
    locked_post.email_dispatch_last_error = None
    if not recipients:
        locked_post.email_dispatch_completed_at = now
    await db.commit()
    return True


async def _recover_interrupted_article_emails(
    db: AsyncSession,
    post_id: UUID,
) -> None:
    """Resolve stale in-flight claims as unknown without risking duplicates."""
    now = utc_now()
    await db.execute(
        update(ContentPostEmailLog)
        .where(
            ContentPostEmailLog.post_id == post_id,
            ContentPostEmailLog.delivery_status == "sending",
            ContentPostEmailLog.last_attempt_at < (now - INTERRUPTED_SEND_TIMEOUT),
        )
        .values(
            delivery_status="unknown",
            error_message="Delivery result unknown after an interrupted send",
            updated_at=now,
        )
    )
    await db.commit()


def _claimable_email_condition(*, force_failed_retry: bool):
    failed_condition = ContentPostEmailLog.delivery_status == "failed"
    if not force_failed_retry:
        failed_condition = and_(
            failed_condition,
            ContentPostEmailLog.attempt_count < MAX_AUTOMATIC_EMAIL_ATTEMPTS,
        )
    return or_(
        ContentPostEmailLog.delivery_status == "pending",
        failed_condition,
    )


async def _claim_article_email(
    db: AsyncSession,
    log_id: UUID,
    *,
    force_failed_retry: bool = False,
) -> UUID | None:
    """Claim one snapshotted delivery before provider I/O."""
    now = utc_now()
    claim_result = await db.execute(
        update(ContentPostEmailLog)
        .where(
            ContentPostEmailLog.id == log_id,
            _claimable_email_condition(force_failed_retry=force_failed_retry),
        )
        .values(
            delivery_status="sending",
            error_message=None,
            attempt_count=ContentPostEmailLog.attempt_count + 1,
            last_attempt_at=now,
            updated_at=now,
        )
        .returning(ContentPostEmailLog.id)
    )
    claimed_id = claim_result.scalar_one_or_none()

    # The claim must be durable before provider I/O. An interrupted claim is
    # later classified as unknown and never retried automatically.
    await db.commit()
    return claimed_id


async def _finish_article_email(
    db: AsyncSession,
    *,
    log_id: UUID,
    delivery_status: str,
    error_message: str | None = None,
) -> None:
    now = utc_now()
    await db.execute(
        update(ContentPostEmailLog)
        .where(
            ContentPostEmailLog.id == log_id,
            ContentPostEmailLog.delivery_status == "sending",
        )
        .values(
            delivery_status=delivery_status,
            sent_at=now if delivery_status == "sent" else None,
            error_message=error_message,
            updated_at=now,
        )
    )
    await db.commit()


async def _finalize_content_email_dispatch(
    db: AsyncSession,
    post: ContentPost,
) -> None:
    result = await db.execute(
        select(ContentPostEmailLog).where(ContentPostEmailLog.post_id == post.id)
    )
    logs = list(result.scalars().all())
    incomplete = any(
        log.delivery_status in {"pending", "sending"}
        or (
            log.delivery_status == "failed"
            and log.attempt_count < MAX_AUTOMATIC_EMAIL_ATTEMPTS
        )
        for log in logs
    )
    failed_count = sum(log.delivery_status == "failed" for log in logs)
    unknown_count = sum(log.delivery_status == "unknown" for log in logs)
    now = utc_now()

    messages = []
    if failed_count:
        messages.append(f"{failed_count} delivery failure(s)")
    if unknown_count:
        messages.append(f"{unknown_count} delivery outcome(s) unknown")

    await db.execute(
        update(ContentPost)
        .where(ContentPost.id == post.id)
        .values(
            email_dispatch_last_attempt_at=now,
            email_dispatch_completed_at=None if incomplete else now,
            email_dispatch_last_error="; ".join(messages) or None,
            updated_at=now,
        )
    )
    await db.commit()


async def send_content_post_publish_emails(
    db: AsyncSession,
    post: ContentPost,
    *,
    force_failed_retry: bool = False,
) -> int:
    """Deliver a published article to its persisted publish-time audience."""
    if not post.email_on_publish or not post.is_published:
        return 0

    if not await _snapshot_article_recipients(db, post):
        return 0

    await _recover_interrupted_article_emails(db, post.id)
    candidates_result = await db.execute(
        select(ContentPostEmailLog)
        .where(
            ContentPostEmailLog.post_id == post.id,
            _claimable_email_condition(force_failed_retry=force_failed_retry),
        )
        .order_by(ContentPostEmailLog.created_at.asc())
    )
    candidates = list(candidates_result.scalars().all())

    featured_image_url = None
    if candidates and post.featured_image_media_id:
        featured_image_url = await resolve_media_url(post.featured_image_media_id)
        if not featured_image_url:
            await _record_dispatch_error(
                db,
                post,
                "Could not resolve the article featured image",
            )
            return 0

    sent_count = 0
    for delivery in candidates:
        log_id = await _claim_article_email(
            db,
            delivery.id,
            force_failed_retry=force_failed_retry,
        )
        if log_id is None:
            continue

        try:
            success = await send_content_post_published_email(
                to_email=delivery.recipient_email or "",
                member_name=delivery.recipient_name or "there",
                post_id=str(post.id),
                title=post.title,
                summary=post.summary,
                category=post.category,
                featured_image_url=featured_image_url,
            )
        except EmailDeliveryUnknownError as exc:
            logger.error(
                "Failed to send content post email post=%s member=%s: %s",
                post.id,
                delivery.member_id,
                exc,
            )
            await _finish_article_email(
                db,
                log_id=log_id,
                delivery_status="unknown",
                error_message=str(exc),
            )
            continue
        except Exception as exc:
            logger.exception(
                "Content email preparation failed post=%s member=%s",
                post.id,
                delivery.member_id,
            )
            await _finish_article_email(
                db,
                log_id=log_id,
                delivery_status="failed",
                error_message=str(exc),
            )
            continue

        if success:
            await _finish_article_email(
                db,
                log_id=log_id,
                delivery_status="sent",
            )
            sent_count += 1
        else:
            await _finish_article_email(
                db,
                log_id=log_id,
                delivery_status="failed",
                error_message="Email provider returned failure",
            )
    await _finalize_content_email_dispatch(db, post)
    logger.info(
        "Sent content post email post=%s to %d member(s)",
        post.id,
        sent_count,
    )
    return sent_count


async def retry_incomplete_content_email_dispatches(
    db: AsyncSession,
    *,
    limit: int = 25,
) -> int:
    """Resume recipient snapshots and known failures left by prior attempts."""
    retry_before = utc_now() - DISPATCH_RETRY_DELAY
    result = await db.execute(
        select(ContentPost)
        .where(
            ContentPost.is_published.is_(True),
            ContentPost.email_on_publish.is_(True),
            ContentPost.email_dispatch_completed_at.is_(None),
            or_(
                ContentPost.email_dispatch_last_attempt_at.is_(None),
                ContentPost.email_dispatch_last_attempt_at <= retry_before,
            ),
        )
        .order_by(ContentPost.published_at.asc())
        .limit(limit)
    )
    posts = list(result.scalars().all())
    for post in posts:
        await send_content_post_publish_emails(db, post)
    return len(posts)


async def publish_scheduled_content() -> None:
    """
    Find and publish all content posts whose scheduled_for <= now
    and are not yet published.
    """
    now = utc_now()

    async for db in get_async_db():
        query = (
            select(ContentPost)
            .where(
                ContentPost.scheduled_for.isnot(None),
                ContentPost.scheduled_for <= now,
                ContentPost.is_published.is_(False),
            )
            .with_for_update(skip_locked=True)
        )
        result = await db.execute(query)
        posts = result.scalars().all()

        if not posts:
            logger.info("No scheduled content posts due for publishing.")
        else:
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

        resumed = await retry_incomplete_content_email_dispatches(db)
        if resumed:
            logger.info("Resumed %d incomplete content email dispatch(es).", resumed)
