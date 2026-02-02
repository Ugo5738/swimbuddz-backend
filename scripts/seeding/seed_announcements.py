#!/usr/bin/env python3
"""
Seed announcements into the database.

This script loads announcements from announcements.json and inserts them
into the database as published announcements.

Usage:
    python scripts/seeding/seed_announcements.py

Note: Requires an admin member to exist in the database (run create_admin.py first).
"""

import asyncio
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from libs.common.config import get_settings
from services.communications_service.models import (
    Announcement,
    AnnouncementAudience,
    AnnouncementCategory,
    AnnouncementStatus,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

settings = get_settings()


async def seed_announcements():
    """Load and insert announcements from JSON file."""
    # Load JSON file
    json_path = Path(__file__).parent / "announcements.json"
    if not json_path.exists():
        print(f"Error: {json_path} not found")
        return

    with open(json_path, "r", encoding="utf-8") as f:
        announcements_data = json.load(f)

    print(f"Loaded {len(announcements_data)} announcements from JSON")

    # Create async engine and session
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        # Check for existing announcements to avoid duplicates
        result = await session.execute(select(Announcement.title))
        existing_titles = {row[0] for row in result.fetchall()}

        created_count = 0
        skipped_count = 0

        for ann_data in announcements_data:
            title = ann_data["title"]

            # Skip if announcement already exists
            if title in existing_titles:
                print(f"  Skipping (exists): {title[:50]}...")
                skipped_count += 1
                continue

            # Map string values to enums
            category_str = ann_data.get("category", "general")
            audience_str = ann_data.get("audience", "community")

            # Convert category string to enum
            try:
                category = AnnouncementCategory(category_str)
            except ValueError:
                category = AnnouncementCategory.GENERAL

            # Convert audience string to enum
            try:
                audience = AnnouncementAudience(audience_str)
            except ValueError:
                audience = AnnouncementAudience.COMMUNITY

            now = datetime.now(timezone.utc)

            # Create published announcement
            announcement = Announcement(
                id=uuid.uuid4(),
                title=title,
                summary=ann_data.get("summary"),
                body=ann_data["body"],
                category=category,
                status=AnnouncementStatus.PUBLISHED,
                audience=audience,
                is_pinned=ann_data.get("is_pinned", False),
                notify_email=False,  # Don't send emails for seeded data
                notify_push=False,
                published_at=now,
                created_at=now,
                updated_at=now,
            )

            session.add(announcement)
            created_count += 1
            print(f"  Created: {title[:50]}...")

        await session.commit()

        print("\nAnnouncements seeding complete:")
        print(f"  Created: {created_count}")
        print(f"  Skipped: {skipped_count}")


if __name__ == "__main__":
    asyncio.run(seed_announcements())
