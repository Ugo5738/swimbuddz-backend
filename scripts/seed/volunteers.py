#!/usr/bin/env python3
"""
Seed default volunteer roles into the database.

This script creates the 13 core volunteer roles defined for the
SwimBuddz community. See docs/VOLUNTEER_ROLES.md for full descriptions.

Usage:
    python scripts/seed/volunteers.py
"""

import asyncio
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from libs.common.config import get_settings
from services.volunteer_service.models import (
    VolunteerRole,
    VolunteerRoleCategory,
    VolunteerTier,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

settings = get_settings()

SEED_ROLES = [
    # ── Session Roles ──────────────────────────────────────────────────
    {
        "title": "Session Lead",
        "description": (
            "Main coordinator for the day's swim session. Arrives 15 min early, "
            "briefs other volunteers, signals warm-up/swim/cool-down phases, "
            "makes announcements, handles on-the-ground issues, and is the last "
            "to leave."
        ),
        "category": VolunteerRoleCategory.SESSION_LEAD,
        "min_tier": VolunteerTier.TIER_2,
        "icon": "📣",
        "sort_order": 1,
    },
    {
        "title": "Warm-up Lead",
        "description": (
            "Leads 15-30 min of dry-land warm-up exercises before swimmers enter "
            "the pool. Demonstrates exercises, adapts for different fitness levels, "
            "focuses on injury prevention. No certification needed."
        ),
        "category": VolunteerRoleCategory.WARMUP_LEAD,
        "min_tier": VolunteerTier.TIER_1,
        "icon": "🏋️",
        "sort_order": 2,
    },
    {
        "title": "Lane Marshal",
        "description": (
            "Manages lane assignments and pool etiquette during the swim. Assigns "
            "lanes by speed/ability, helps beginners find placement, enforces "
            "circle swimming, and rebalances lanes when needed."
        ),
        "category": VolunteerRoleCategory.LANE_MARSHAL,
        "min_tier": VolunteerTier.TIER_1,
        "icon": "🚩",
        "sort_order": 3,
    },
    {
        "title": "Check-in Volunteer",
        "description": (
            "Handles arrival registration and attendance tracking. Sets up 15 min "
            "before start, marks members present on the app, confirms walk-ins vs "
            "pre-registered, notes first-timers, and tracks late arrivals."
        ),
        "category": VolunteerRoleCategory.CHECKIN,
        "min_tier": VolunteerTier.TIER_1,
        "icon": "📋",
        "sort_order": 4,
    },
    {
        "title": "Safety Rep",
        "description": (
            "Monitors swimmer wellbeing during sessions. Positioned poolside with "
            "clear view of all lanes. Knows emergency procedures, first-aid kit "
            "location, and nearest hospital. Flags exhaustion, distress, or "
            "unsafe behaviour."
        ),
        "category": VolunteerRoleCategory.SAFETY,
        "min_tier": VolunteerTier.TIER_2,
        "icon": "🛡️",
        "sort_order": 5,
    },
    # ── Community Roles ────────────────────────────────────────────────
    {
        "title": "Welcome Volunteer",
        "description": (
            "First friendly face for newcomers. Gives orientation on changing "
            "rooms, belongings, and session flow. Introduces first-timers to "
            "regulars, answers basic questions, and checks in after the session."
        ),
        "category": VolunteerRoleCategory.WELCOME,
        "min_tier": VolunteerTier.TIER_1,
        "icon": "👋",
        "sort_order": 6,
    },
    {
        "title": "Ride Share Lead",
        "description": (
            "Drives community members to and from swim sessions. Coordinates "
            "pickup times and locations with ride group, communicates departure "
            "updates, and ensures safe transport. Fuel contributions may apply."
        ),
        "category": VolunteerRoleCategory.RIDE_SHARE,
        "min_tier": VolunteerTier.TIER_1,
        "icon": "🚗",
        "sort_order": 7,
    },
    {
        "title": "Mentor / Buddy",
        "description": (
            "Pairs with newer or anxious swimmers for 4-8 sessions. Checks in "
            "before sessions, swims nearby as a familiar face, explains community "
            "norms, celebrates milestones, and reaches out if they go quiet."
        ),
        "category": VolunteerRoleCategory.MENTOR,
        "min_tier": VolunteerTier.TIER_2,
        "icon": "🤝",
        "sort_order": 8,
    },
    # ── Content & Media Roles ──────────────────────────────────────────
    {
        "title": "Media Volunteer",
        "description": (
            "Captures photos and videos during sessions and events. Action shots, "
            "warm-up photos, candid moments, and the group photo at the end. "
            "Respects photo consent opt-outs. Shares raw media with gallery team."
        ),
        "category": VolunteerRoleCategory.MEDIA,
        "min_tier": VolunteerTier.TIER_1,
        "icon": "📸",
        "sort_order": 9,
    },
    {
        "title": "Gallery Support",
        "description": (
            "Organises and uploads session media to the SwimBuddz platform. Tags "
            "members in photos, organises by date/event, selects highlights for "
            "socials, and removes images of opted-out members. Can be done remotely."
        ),
        "category": VolunteerRoleCategory.GALLERY_SUPPORT,
        "min_tier": VolunteerTier.TIER_1,
        "icon": "🖼️",
        "sort_order": 10,
    },
    # ── Events & Logistics Roles ───────────────────────────────────────
    {
        "title": "Events & Logistics Volunteer",
        "description": (
            "Behind-the-scenes operator for special events. Helps with setup/"
            "teardown, manages equipment (cones, lane ropes, speakers), coordinates "
            "timing and transitions, and handles on-the-ground logistics."
        ),
        "category": VolunteerRoleCategory.EVENTS_LOGISTICS,
        "min_tier": VolunteerTier.TIER_1,
        "icon": "📦",
        "sort_order": 11,
    },
    {
        "title": "Trip Planner",
        "description": (
            "Organises out-of-town swims, beach days, and destination trips. "
            "Researches venues, plans logistics and budgets, manages RSVPs and "
            "payments, coordinates with local contacts, and runs the day-of."
        ),
        "category": VolunteerRoleCategory.TRIP_PLANNER,
        "min_tier": VolunteerTier.TIER_2,
        "icon": "🗺️",
        "sort_order": 12,
    },
    # ── Academy Support Roles ──────────────────────────────────────────
    {
        "title": "Academy Assistant",
        "description": (
            "Supports coaches during structured academy lessons. Helps demonstrate "
            "drills, works one-on-one with students needing extra attention, "
            "assists with skill assessments, and shadows coaches to learn teaching "
            "methods."
        ),
        "category": VolunteerRoleCategory.ACADEMY_ASSISTANT,
        "min_tier": VolunteerTier.TIER_2,
        "icon": "🎓",
        "sort_order": 13,
    },
]


async def seed_volunteer_roles():
    """Insert default volunteer roles into the database."""
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        # Check for existing roles by category to handle renames gracefully
        result = await session.execute(
            select(VolunteerRole.title, VolunteerRole.category)
        )
        existing = {row[0]: row[1] for row in result.fetchall()}
        existing_titles = set(existing.keys())
        existing_categories = set(existing.values())

        created_count = 0
        skipped_count = 0

        now = datetime.now(timezone.utc)

        for role_data in SEED_ROLES:
            title = role_data["title"]
            category = role_data["category"]

            if title in existing_titles:
                print(f"  Skipping (exists): {title}")
                skipped_count += 1
                continue

            role = VolunteerRole(
                id=uuid.uuid4(),
                title=title,
                description=role_data["description"],
                category=category,
                min_tier=role_data.get("min_tier", VolunteerTier.TIER_1),
                icon=role_data.get("icon"),
                sort_order=role_data.get("sort_order", 0),
                is_active=True,
                created_at=now,
                updated_at=now,
            )
            session.add(role)
            created_count += 1
            print(f"  Created: {title}")

        await session.commit()

        print("\nVolunteer roles seeding complete:")
        print(f"  Created: {created_count}")
        print(f"  Skipped: {skipped_count}")


if __name__ == "__main__":
    asyncio.run(seed_volunteer_roles())
