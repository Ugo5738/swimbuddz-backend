#!/usr/bin/env python3
"""
Seed content posts into the database.

This script loads content posts from content_posts.json and inserts them
into the database as draft posts. The admin can then edit them in the
frontend to add featured images and publish when ready.

Usage:
    python scripts/seed/content-posts.py

Note: Requires an admin member to exist in the database. The script will
look up the first admin user or use a specific email if configured.
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
from services.communications_service.models import ContentPost
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

settings = get_settings()


async def get_admin_member_id(session: AsyncSession) -> uuid.UUID:
    """
    Get the admin member ID to use as created_by.
    Looks for a member with admin role or uses the first member with admin email.
    """
    # Import MemberRef to query members table
    from services.members_service.models import Member

    # Try to find admin by email
    admin_emails = settings.ADMIN_EMAILS or []
    if admin_emails:
        result = await session.execute(
            select(Member).where(Member.email.in_(admin_emails)).limit(1)
        )
        admin = result.scalar_one_or_none()
        if admin:
            print(f"  Found admin member: {admin.email}")
            return admin.id

    # Fallback: get the first member (assuming first registered is admin)
    result = await session.execute(
        select(Member).order_by(Member.created_at.asc()).limit(1)
    )
    first_member = result.scalar_one_or_none()
    if first_member:
        print(f"  Using first member as creator: {first_member.email}")
        return first_member.id

    raise ValueError(
        "No members found in database. Please create an admin member first."
    )


async def seed_content_posts():
    """Load and insert content posts from JSON file."""
    # Load JSON file from seed-data directory
    json_path = Path(__file__).parent.parent / "seed-data" / "content_posts.json"
    if not json_path.exists():
        print(f"Error: {json_path} not found")
        return

    with open(json_path, "r", encoding="utf-8") as f:
        posts_data = json.load(f)

    print(f"Loaded {len(posts_data)} content posts from JSON")

    # Create async engine and session
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        # Get admin member ID
        try:
            admin_id = await get_admin_member_id(session)
        except ValueError as e:
            print(f"Error: {e}")
            return

        # Check for existing posts to avoid duplicates
        result = await session.execute(select(ContentPost.title))
        existing_titles = {row[0] for row in result.fetchall()}

        created_count = 0
        skipped_count = 0

        for post_data in posts_data:
            title = post_data["title"]

            # Skip if post already exists
            if title in existing_titles:
                print(f"  Skipping (exists): {title[:50]}...")
                skipped_count += 1
                continue

            # Create post as draft (not published)
            # featured_image_prompt is NOT stored - it's just for reference
            post = ContentPost(
                id=uuid.uuid4(),
                title=title,
                summary=post_data.get("summary"),
                body=post_data["body"],
                category=post_data.get("category", "getting_started"),
                tier_access=post_data.get("tier_access", "community"),
                featured_image_media_id=None,  # Admin will add later
                is_published=False,  # Start as draft
                published_at=None,
                created_by=admin_id,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )

            session.add(post)
            created_count += 1
            print(f"  Created: {title[:50]}...")

        await session.commit()

        print("\nContent posts seeding complete:")
        print(f"  Created: {created_count}")
        print(f"  Skipped: {skipped_count}")
        print("\nNote: Posts are created as drafts. Use the admin panel to:")
        print("  1. Add featured images")
        print("  2. Review and edit content")
        print("  3. Publish when ready")


if __name__ == "__main__":
    asyncio.run(seed_content_posts())
