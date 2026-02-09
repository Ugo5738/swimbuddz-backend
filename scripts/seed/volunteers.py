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
        "time_commitment": "90–120 min (full session + 15 min before/after)",
        "responsibilities": [
            "Arrive 15 minutes before session starts",
            "Brief other volunteers on the day's plan",
            "Signal the start and end of each phase: warm-up, main swim, cool-down",
            "Make announcements to the group",
            "Handle any on-the-ground issues",
            "Ensure all swimmers have exited before leaving",
        ],
        "skills_needed": (
            "Comfortable speaking to groups. Calm under mild pressure. "
            "No swimming expertise required."
        ),
        "best_for": (
            "People who like organising, keeping things on track, "
            "and being the go-to person."
        ),
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
        "time_commitment": "~45 min (10 min setup + 30 min warm-up + 5 min wrap-up)",
        "responsibilities": [
            "Prepare a 15–30 minute warm-up routine",
            "Lead stretches, mobility drills, and light cardio",
            "Demonstrate each exercise clearly",
            "Adapt for different fitness levels",
            "Focus on injury prevention: shoulders, neck, ankles, core",
        ],
        "skills_needed": "Basic fitness knowledge. Enthusiasm. No certification needed.",
        "best_for": (
            "Fitness enthusiasts, gym-goers, anyone who enjoys leading "
            "group exercise."
        ),
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
        "time_commitment": "~40–50 min (main swim portion)",
        "responsibilities": [
            "Assign swimmers to lanes based on speed and ability",
            "Help first-timers understand circle swimming",
            "Rebalance lanes if one gets overcrowded",
            "Gently enforce lane etiquette",
            "Watch for swimmers in the wrong lane and help them move",
        ],
        "skills_needed": "Basic swimming knowledge. Tactful communication.",
        "best_for": (
            "Experienced swimmers who understand pool flow and can guide others."
        ),
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
        "time_commitment": "~30 min (15 min before + first 15 min of session)",
        "responsibilities": [
            "Set up at the session entrance before start time",
            "Greet arriving members and mark attendance on the app",
            "Confirm walk-ins vs. pre-registered members",
            "Note first-timers and direct them to the Welcome Volunteer",
            "Track late arrivals and hand off count to Session Lead",
        ],
        "skills_needed": (
            "Comfortable using a phone/tablet. Friendly and approachable."
        ),
        "best_for": ("Organised people who like greeting others. Low physical effort."),
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
        "time_commitment": "~50–60 min (full swim duration, focused attention)",
        "responsibilities": [
            "Position yourself poolside with clear view of all lanes",
            "Watch for signs of exhaustion, distress, or unsafe behaviour",
            "Know the location of first-aid kit, AED, and emergency exits",
            "Alert nearest coach or lifeguard if someone is struggling",
            "Flag hazards to the Session Lead",
        ],
        "skills_needed": (
            "Basic first-aid knowledge preferred. Must be attentive and calm. "
            "CPR training is a plus."
        ),
        "best_for": (
            "Responsible, observant individuals. Healthcare workers, parents."
        ),
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
        "time_commitment": "~30 min spread across the session",
        "responsibilities": [
            "Introduce yourself to anyone attending their first session",
            "Give a quick orientation: changing rooms, belongings, session flow",
            "Introduce newcomers to 2–3 friendly regulars",
            "Answer basic questions",
            "Check in with them at the end — encourage them to come back",
        ],
        "skills_needed": ("Warmth. Friendliness. Memory for names is a huge plus."),
        "best_for": (
            "Extroverts, natural connectors, people who remember what it "
            "felt like to be new."
        ),
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
        "time_commitment": "30–60 min each way (Lagos traffic considered)",
        "responsibilities": [
            "Make your car available for a designated pickup zone",
            "Communicate departure time and pickup location",
            "Wait a reasonable time for passengers (5 min grace period)",
            "Drive safely to the pool venue",
            "After session, drive passengers back to pickup zone",
        ],
        "skills_needed": (
            "Valid driver's license. Reliable vehicle. Patience in Lagos traffic."
        ),
        "best_for": "Car owners already driving to sessions with spare seats.",
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
        "time_commitment": (
            "No extra time beyond attending sessions + 5–10 min " "WhatsApp check-ins"
        ),
        "responsibilities": [
            "Be paired with a newer member for 4–8 sessions",
            "Check in before each session — are they coming? Any concerns?",
            "Swim near them so they have a familiar face",
            "Help them understand community norms",
            "Celebrate their milestones and reach out if they go quiet",
        ],
        "skills_needed": (
            "Empathy. Consistency. Patience with people who may be " "scared of water."
        ),
        "best_for": ("Regulars who remember how intimidating it was to start."),
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
        "time_commitment": "Throughout the session (can still swim)",
        "responsibilities": [
            "Capture a mix of action shots, warm-up photos, candid moments, "
            "group shots",
            "Record 15–30 second video clips for social content",
            "Get the group photo at the end of every session",
            "Respect photo consent — skip opted-out members",
            "Share raw media with Gallery Support or upload to shared album",
        ],
        "skills_needed": "A decent phone camera. Basic sense of composition.",
        "best_for": (
            "Anyone already snapping pics at sessions. Instagram-savvy members."
        ),
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
        "time_commitment": "30–60 min after each session (can be done from home)",
        "responsibilities": [
            "Collect raw photos/videos from Media Volunteers",
            "Upload them to the SwimBuddz gallery",
            "Tag members who appear in photos",
            "Select 5–10 best-of shots per session for highlights",
            "Delete duplicates and ensure photo consent is respected",
        ],
        "skills_needed": (
            "Organised. Eye for selecting good photos. Can be done remotely."
        ),
        "best_for": (
            "Detail-oriented people. Photographers. Great if you can't "
            "always attend sessions."
        ),
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
        "time_commitment": "2–4 hours per event (variable)",
        "responsibilities": [
            "Help set up and tear down for special events",
            "Manage equipment: cones, lane ropes, timing equipment, speakers",
            "Coordinate timing between activities during multi-part events",
            "Handle on-the-ground logistics: venue access, parking, vendors",
            "Be the fixer — adapt when things go wrong",
        ],
        "skills_needed": (
            "Flexible. Problem-solver. Comfortable with physical setup work."
        ),
        "best_for": "People who like making things happen behind the scenes.",
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
        "time_commitment": ("5–10 hours planning per trip (over 2–4 weeks) + trip day"),
        "responsibilities": [
            "Research and propose trip destinations",
            "Plan logistics: transport, accommodation, costs, group size",
            "Create and share trip itineraries",
            "Manage RSVPs and collect payments",
            "Handle on-the-day logistics and safety briefings",
        ],
        "skills_needed": (
            "Research skills. Organisational ability. Budget management."
        ),
        "best_for": ("Natural planners. Travel enthusiasts who know great swim spots."),
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
        "time_commitment": ("60–90 min (full academy session) + 10 min coach debrief"),
        "responsibilities": [
            "Assist the lead coach during cohort sessions",
            "Help demonstrate drills and techniques",
            "Work one-on-one with students needing extra attention",
            "Assist with skill assessment sessions",
            "Help manage session resources: kickboards, pull buoys, fins",
        ],
        "skills_needed": (
            "Competent swimmer (Intermediate+). Patient with learners. "
            "Interest in coaching."
        ),
        "best_for": (
            "Strong swimmers interested in coaching. Education professionals."
        ),
    },
]

# Fields that can be updated on existing roles
UPDATABLE_FIELDS = [
    "description",
    "min_tier",
    "icon",
    "sort_order",
    "time_commitment",
    "responsibilities",
    "skills_needed",
    "best_for",
]


async def seed_volunteer_roles():
    """Insert or update default volunteer roles in the database."""
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        # Fetch existing roles keyed by title
        result = await session.execute(select(VolunteerRole))
        existing_roles = {role.title: role for role in result.scalars().all()}

        created_count = 0
        updated_count = 0
        skipped_count = 0

        now = datetime.now(timezone.utc)

        for role_data in SEED_ROLES:
            title = role_data["title"]

            if title in existing_roles:
                # Update existing role with any new/changed fields
                existing = existing_roles[title]
                changed = False
                for field in UPDATABLE_FIELDS:
                    if field in role_data:
                        new_val = role_data[field]
                        if getattr(existing, field) != new_val:
                            setattr(existing, field, new_val)
                            changed = True
                if changed:
                    existing.updated_at = now
                    updated_count += 1
                    print(f"  Updated: {title}")
                else:
                    skipped_count += 1
                    print(f"  Skipping (up to date): {title}")
                continue

            role = VolunteerRole(
                id=uuid.uuid4(),
                title=title,
                description=role_data["description"],
                category=role_data["category"],
                min_tier=role_data.get("min_tier", VolunteerTier.TIER_1),
                icon=role_data.get("icon"),
                sort_order=role_data.get("sort_order", 0),
                is_active=True,
                time_commitment=role_data.get("time_commitment"),
                responsibilities=role_data.get("responsibilities"),
                skills_needed=role_data.get("skills_needed"),
                best_for=role_data.get("best_for"),
                created_at=now,
                updated_at=now,
            )
            session.add(role)
            created_count += 1
            print(f"  Created: {title}")

        await session.commit()

        print("\nVolunteer roles seeding complete:")
        print(f"  Created: {created_count}")
        print(f"  Updated: {updated_count}")
        print(f"  Skipped: {skipped_count}")


if __name__ == "__main__":
    asyncio.run(seed_volunteer_roles())
