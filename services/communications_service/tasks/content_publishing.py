"""
Background task for scheduled content post publishing.

Publishes content posts whose scheduled_for time has arrived.
Runs every hour via ARQ cron to catch any posts due for publishing.
"""

from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from libs.db.session import get_async_db
from sqlalchemy import select

from services.communications_service.models import ContentPost

logger = get_logger(__name__)


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
            post.is_published = True
            post.published_at = now
            logger.info(
                "Auto-published content post: %s (scheduled_for=%s)",
                post.title,
                post.scheduled_for,
            )

        await db.commit()
        logger.info("Published %d scheduled content post(s).", len(posts))
