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
        "time_commitment": "Full session duration + 15 min before and after (~90–120 min total)",
        "responsibilities": [
            "Arrive 15 minutes before the session starts.",
            "Brief other volunteers on the day's plan (swimmers expected, first-timers, special announcements).",
            "Signal the start and end of each phase: warm-up, main swim, cool-down.",
            "Make announcements to the group (upcoming events, reminders, shout-outs).",
            "Handle any on-the-ground issues (late arrivals, lane changes, schedule adjustments).",
            "Ensure all swimmers have exited the pool area before you leave.",
            "Be the last volunteer to leave.",
        ],
        "skills_needed": (
            "Comfortable speaking to groups. Calm under mild pressure. "
            "No swimming expertise required, you're coordinating, not coaching"
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
        "time_commitment": "~45 min (arrive 10 min early to set up, 25 min warm-up, 5 min wrap-up)",
        "responsibilities": [
            "Prepare a 15–30 minute warm-up routine (templates provided or freestyle).",
            "Lead the group through stretches, mobility drills, and light cardio before entering the pool.",
            "Demonstrate each exercise clearly facing the group, count reps out loud.",
            "Adapt on the fly for different fitness levels (offer easier/harder variations).",
            "Focus on injury prevention: shoulders, neck, ankles, and core.",
            "Keep energy high through music, light banter, encouragement.",
        ],
        "skills_needed": "Basic fitness knowledge. Enthusiasm. You do NOT need a personal training certification, just be comfortable leading a group through simple exercises.",
        "best_for": (
            "Fitness enthusiasts, gym-goers, anyone who enjoys leading group exercise. Great entry-level volunteer role."
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
        "time_commitment": "Main swim portion only (~40–50 min)",
        "responsibilities": [
            "Assign swimmers to lanes based on speed and ability (fast, medium, slow, beginner).",
            "Help first-timers understand circle swimming (swim down one side, return on the other).",
            "Rebalance lanes if one gets overcrowded mid-session.",
            "Gently enforce lane etiquette: no stopping at the wall for long chats, yielding to faster swimmers, etc.",
            "Watch for swimmers in the wrong lane and help them move without embarrassment.",
        ],
        "skills_needed": "Basic swimming knowledge (you need to understand lane speed groupings). Tactful communication — you'll be redirecting people politely.",
        "best_for": (
            "Experienced swimmers who understand pool flow and can guide others without being bossy."
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
        "time_commitment": "~30 min (15 min before session + first 15 min of session)",
        "responsibilities": [
            "Set up at the session entrance 15 minutes before start time.",
            "Greet arriving members and mark them as 'present' on the SwimBuddz app.",
            "Confirm walk-ins vs. pre-registered members.",
            "Note any guests or first-timers and direct them to the Welcome Volunteer.",
            "Track late arrivals.",
            "Hand off the final attendance count to the Session Lead.",
        ],
        "skills_needed": (
            "Comfortable using a phone/tablet. Friendly and approachable. Detail-oriented."
        ),
        "best_for": (
            "Organised people who like greeting others. Low physical effort, you're at a table, not in the pool."
        ),
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
        "time_commitment": "Full swim duration (~50–60 min of focused attention)",
        "responsibilities": [
            "Position yourself poolside with a clear view of all lanes during the swim.",
            "Watch for signs of exhaustion, distress, or unsafe behaviour (diving in shallow areas, horseplay).",
            "Know the location of the first-aid kit, AED (if available), and emergency exits.",
            "Know the venue's emergency phone number and nearest hospital.",
            "If someone is struggling, alert the nearest coach or lifeguard immediately.",
            "Flag any hazards (slippery deck, broken equipment, overcrowding) to the Session Lead.",
            "Ensure no one is left in the pool unattended.",
        ],
        "skills_needed": (
            "Basic first-aid knowledge is strongly preferred but not mandatory. Must be attentive and calm. CPR training is a plus, we'll help you get certified if you're interested."
        ),
        "best_for": (
            "Responsible, observant individuals. Healthcare workers, parents, anyone who naturally watches out for others."
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
        "time_commitment": "~30 min spread across the session (before, during breaks, after)",
        "responsibilities": [
            "Introduce yourself to anyone attending their first session.",
            "Give them a quick orientation: where to change, where to store belongings, how the session flows.",
            "Introduce them to 2–3 friendly regulars so they don't feel alone.",
            'Answer basic questions ("Do I need goggles?", "Which lane should I be in?", "Is there food after?").',
            "Check in with them at the end of the session, ask how it went, encourage them to come back.",
            "If they seem nervous about the water, connect them with a Mentor/Buddy.",
        ],
        "skills_needed": ("Warmth. Friendliness. Memory for names is a huge plus."),
        "best_for": (
            "Extroverts, natural connectors, people who remember what it felt like to be new."
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
        "time_commitment": "Variable, depends on distance. Typically 30–60 min each way (Lagos traffic considered).",
        "responsibilities": [
            "Make your car available for a designated pickup zone (Yaba, VI, Ikoyi, Lekki, etc.).",
            "Communicate your departure time and exact pickup location to your ride group via WhatsApp or the app.",
            "Wait a reasonable time for your passengers (5 min grace period from announced departure).",
            "Drive safely to the pool venue.",
            "After the session, drive your passengers back to the pickup zone (or an agreed drop-off).",
            "Report any issues (no-shows from passengers, traffic delays) via the app.",
        ],
        "skills_needed": (
            "Valid driver's license. Reliable vehicle. Patience in Lagos traffic. Good communication, you need to coordinate timing with 2–4 people."
        ),
        "best_for": "Car owners who are already driving to the session and have spare seats.",
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
            "No extra time beyond attending sessions you'd already attend. Plus 5–10 min of WhatsApp check-ins between sessions."
        ),
        "responsibilities": [
            "Be paired with a newer member (or someone returning after a long break) for 4–8 sessions.",
            "Check in with them before each session, are they coming? Do they need a ride? Any concerns?",
            "Swim near them during sessions so they have a familiar face.",
            "Help them understand the community norms (when sessions happen, how to sign up, WhatsApp group etiquette).",
            "Celebrate their milestones: first full session, first 10 sessions, learning a new stroke.",
            "If they miss 2+ sessions in a row, reach out, a simple 'Hey, we missed you!' goes far.",
            "Help transition them to independence after 4–8 sessions, introduce them to others, help them find their own rhythm.",
        ],
        "skills_needed": (
            "Empathy. Consistency (you need to show up to the sessions your mentee is attending). Patience with people who may be scared of water."
        ),
        "best_for": (
            "Regulars who remember how intimidating it was to start. People who naturally check on others."
        ),
    },
    {
        "title": "Group Admin",
        "description": (
            "Keeps the WhatsApp groups active, organized, informed and motivated."
        ),
        "category": VolunteerRoleCategory.OTHER,
        "min_tier": VolunteerTier.TIER_2,
        "icon": "🤝",
        "sort_order": 9,
        "time_commitment": ("~1–2 hours per week (spread out in 5-minute increments)."),
        "responsibilities": [
            "Post the weekly session registration links and reminders (templates provided).",
            "Answer basic questions in the chat ('Is the session holding?', 'Where do I park?', 'What time do we start?').",
            "Keep the vibe positive and inclusive; ensure conversations stay respectful.",
            "Post the 'post-swim' group photo to the chat after sessions to keep engagement high.",
            "Nudge the group if sign-ups are low for an upcoming session: 'We have 3 spots left for Saturday!'",
        ],
        "skills_needed": (
            "Responsive on WhatsApp. Friendly tone. Patience. Good with gifs/memes (optional but helpful)."
        ),
        "best_for": (
            "People who are always on their phone, remote workers, or those who want to help."
        ),
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
        "sort_order": 10,
        "time_commitment": "Throughout the session. You can still swim, just keep your phone nearby for key moments.",
        "responsibilities": [
            "Bring your phone (or camera if you have one) to the session.",
            "Capture a mix of: action shots in the pool, warm-up photos, candid moments, group shots after the swim.",
            "Record 15–30 second video clips for Instagram/TikTok content.",
            "Get the 'money shot' — the group photo at the end of every session.",
            "Respect photo consent — if someone has opted out, don't include them in shots.",
            "Share raw media with the Gallery Support volunteer or upload directly to the shared album.",
            "Optionally: shoot short testimonial videos with willing members.",
        ],
        "skills_needed": "A decent phone camera. Basic sense of composition (we're not looking for professional photography, just good, authentic content). Awareness of lighting and angles.",
        "best_for": (
            "Anyone who's already snapping pics at sessions. Instagram-savvy members. Aspiring content creators."
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
        "sort_order": 11,
        "time_commitment": "30–60 min after each session (can be done from home)",
        "responsibilities": [
            "Collect raw photos/videos from Media Volunteers after each session.",
            "Upload them to the SwimBuddz gallery (via the admin panel or shared album).",
            "Tag members who appear in photos (helps members find their own pictures).",
            "Organise media by date, session, and event.",
            "Select 5–10 'best of' shots per session for social media highlights.",
            "Delete duplicates, blurry shots, and unflattering images.",
            "Ensure photo consent is respected, remove images of members who've opted out.",
        ],
        "skills_needed": (
            "Organised. Familiar with the SwimBuddz admin panel (we'll train you). Eye for selecting good photos. Can be done from anywhere, you don't need to attend the session."
        ),
        "best_for": (
            "Detail-oriented people. Photographers. Anyone who likes curating content. Great role if you can't always make it to sessions physically."
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
        "sort_order": 12,
        "time_commitment": "Variable, depends on the event. Could be 2–4 hours for a small social or a full day for a community meet.",
        "responsibilities": [
            "Help set up and tear down for special events (beach days, social gatherings, watch parties, community meets).",
            "Manage equipment: cones, lane ropes, timing equipment, speakers, banners.",
            "Coordinate timing and transitions between activities during multi-part events.",
            "Handle on-the-ground logistics: venue access, parking coordination, vendor liaison.",
            "Be the 'fixer' - if something goes wrong (missing equipment, late vendor, weather change), you adapt.",
        ],
        "skills_needed": (
            "Flexible. Problem-solver. Comfortable with physical setup work (carrying equipment, arranging spaces). Good under pressure."
        ),
        "best_for": "People who like making things happen behind the scenes. Operations-minded individuals. Anyone who's organised a party, wedding, or community event before.",
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
        "sort_order": 13,
        "time_commitment": (
            "5–10 hours of planning per trip (spread over 2–4 weeks). Plus the trip day itself."
        ),
        "responsibilities": [
            "Research and propose trip destinations (beach locations, open water venues, pools in other cities).",
            "Plan logistics: transport options, accommodation (for overnight trips), costs, group size limits.",
            "Create and share trip itineraries with the community.",
            "Manage RSVPs and collect payments (via the platform or coordination with admin).",
            "Coordinate with local contacts at destination venues.",
            "Handle on-the-day logistics: meeting points, headcounts, safety briefing for open water.",
            "Post-trip: collect feedback, share photos, document lessons for future trips.",
        ],
        "skills_needed": (
            "Research skills. Organisational ability. Budget management. Good communication. Experience travelling within Nigeria is a major plus."
        ),
        "best_for": (
            "Natural planners. Travel enthusiasts. People who already know great swim spots around Lagos and Nigeria."
        ),
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
        "sort_order": 14,
        "time_commitment": (
            "Full academy session duration (~60–90 min) + 10 min debrief with coach."
        ),
        "responsibilities": [
            "Assist the lead coach during cohort sessions (Beginner, Intermediate, Advanced programs).",
            "Help demonstrate drills and techniques alongside the coach.",
            "Work one-on-one with students who need extra attention during group sessions.",
            "Assist with skill assessment sessions: help position students, operate timing equipment, record results.",
            "Shadow experienced coaches to learn teaching methods (this is a pathway to becoming a coach).",
            "Help manage session resources: kickboards, pull buoys, fins, and other training aids.",
            "Provide feedback to the coach after sessions on student progress you observed.",
        ],
        "skills_needed": (
            "Competent swimmer (Intermediate level minimum). Patient with learners. Comfortable in the water giving hands-on guidance. Interest in coaching/teaching is strongly preferred."
        ),
        "best_for": (
            "Strong swimmers interested in coaching. Education professionals. Parents experienced with teaching children to swim. Anyone on the path to becoming a certified swim coach."
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
