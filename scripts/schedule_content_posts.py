#!/usr/bin/env python3
"""
Set scheduled_for dates on unpublished content posts.

Assigns one post per Wednesday at 7 AM WAT (6 AM UTC), starting from
the next Wednesday, in publish_order from the seed data.

Usage:
    # Dry run (default):
    DATABASE_URL=... python scripts/schedule_content_posts.py

    # Apply to DB:
    DATABASE_URL=... python scripts/schedule_content_posts.py --apply
"""

import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from libs.common.config import get_settings
from services.communications_service.models import ContentPost
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

settings = get_settings()
SEED_PATH = Path(__file__).parent / "seed-data" / "content_posts.json"

# 7 AM WAT = 6 AM UTC
PUBLISH_HOUR_UTC = 6


def next_wednesday(from_date: datetime) -> datetime:
    """Get the next Wednesday at 6:00 UTC from a given date."""
    days_ahead = 2 - from_date.weekday()  # Wednesday = 2
    if days_ahead <= 0:
        days_ahead += 7
    next_wed = from_date + timedelta(days=days_ahead)
    return next_wed.replace(
        hour=PUBLISH_HOUR_UTC, minute=0, second=0, microsecond=0, tzinfo=timezone.utc
    )


async def main():
    apply = "--apply" in sys.argv

    # Load seed data to get publish_order
    with open(SEED_PATH, "r", encoding="utf-8") as f:
        seed_posts = json.load(f)
    order_by_title = {p["title"]: p.get("publish_order", 999) for p in seed_posts}

    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        # Get all unpublished posts
        result = await session.execute(
            select(ContentPost).where(ContentPost.is_published.is_(False))
        )
        unpublished = result.scalars().all()

        if not unpublished:
            print("No unpublished posts found.")
            await engine.dispose()
            return

        # Sort by publish_order from seed data
        unpublished.sort(key=lambda p: order_by_title.get(p.title, 999))

        # Calculate dates starting from next Wednesday
        now = datetime.now(timezone.utc)
        start_date = next_wednesday(now)

        print(f"Now: {now.strftime('%Y-%m-%d %H:%M %Z')}")
        print(
            f"First publish date: {start_date.strftime('%Y-%m-%d %H:%M %Z')} (Wednesday 7 AM WAT)"
        )
        print(f"Unpublished posts to schedule: {len(unpublished)}")
        print()

        for i, post in enumerate(unpublished):
            publish_date = start_date + timedelta(weeks=i)
            order = order_by_title.get(post.title, "?")
            current = post.scheduled_for

            if current:
                status = f"  ALREADY SCHEDULED: {current.strftime('%Y-%m-%d %H:%M')}"
            else:
                status = "  NEW"

            print(
                f"  #{order:>2} | {publish_date.strftime('%a %Y-%m-%d %H:%M UTC')} | {post.title}{status if current else ''}"
            )

            if apply:
                post.scheduled_for = publish_date

        if apply:
            await session.commit()
            print(f"\nDone. {len(unpublished)} posts scheduled.")
        else:
            print("\nDRY RUN — use --apply to set these dates in the DB.")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
