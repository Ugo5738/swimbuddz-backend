#!/usr/bin/env python3
"""
Seed default reward rules.

PRODUCTION-SAFE: This script only creates RewardRule records.
It does NOT create test wallets, transactions, or any other test data.

Idempotent: skips if rules already exist. Uses stable UUIDs so
re-runs never create duplicates.
"""

import asyncio
import os
import sys
import uuid

# Add project root to path
project_root = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
sys.path.insert(0, project_root)

from libs.db.config import AsyncSessionLocal
from services.wallet_service.models.enums import RewardCategory, RewardPeriod
from services.wallet_service.models.rewards import RewardRule
from sqlalchemy import func
from sqlalchemy.future import select

# ---------------------------------------------------------------------------
# Default reward rules (22 rules from design doc)
# ---------------------------------------------------------------------------


def build_default_rules() -> list[RewardRule]:
    """Return the 22 default reward rules with stable UUIDs."""
    return [
        # --- ACQUISITION ---
        RewardRule(
            id=uuid.UUID("00000000-0000-0000-0000-100000000001"),
            rule_name="referral_qualified",
            display_name="Referral Reward",
            event_type="referral.qualified",
            trigger_config={"target": "referrer"},
            reward_bubbles=15,
            reward_description_template="Referral reward — {referee_name} joined ({amount} 🫧)",
            max_per_member_lifetime=50,
            category=RewardCategory.ACQUISITION,
            created_by="seed",
        ),
        RewardRule(
            id=uuid.UUID("00000000-0000-0000-0000-100000000002"),
            rule_name="referral_referee_bonus",
            display_name="Referral Welcome Bonus",
            event_type="referral.qualified",
            trigger_config={"target": "referee"},
            reward_bubbles=10,
            reward_description_template="Referral bonus — invited by {referrer_name} ({amount} 🫧)",
            max_per_member_lifetime=1,
            category=RewardCategory.ACQUISITION,
            created_by="seed",
        ),
        RewardRule(
            id=uuid.UUID("00000000-0000-0000-0000-100000000003"),
            rule_name="referral_ambassador_10",
            display_name="Ambassador Milestone (10 referrals)",
            event_type="referral.milestone",
            trigger_config={"milestone_count": 10},
            reward_bubbles=50,
            reward_description_template="Ambassador bonus — 10 successful referrals! ({amount} 🫧)",
            max_per_member_lifetime=1,
            category=RewardCategory.ACQUISITION,
            created_by="seed",
        ),
        RewardRule(
            id=uuid.UUID("00000000-0000-0000-0000-100000000004"),
            rule_name="event_social_share",
            display_name="Event Social Share",
            event_type="event.shared",
            trigger_config={},
            reward_bubbles=2,
            reward_description_template="Reward — shared {event_name} on social media ({amount} 🫧)",
            max_per_member_per_period=5,
            period=RewardPeriod.MONTH,
            category=RewardCategory.ACQUISITION,
            created_by="seed",
        ),
        # --- RETENTION ---
        RewardRule(
            id=uuid.UUID("00000000-0000-0000-0000-100000000005"),
            rule_name="monthly_attendance_4",
            display_name="Monthly Swim Streak (4 sessions)",
            event_type="attendance.monthly_milestone",
            trigger_config={"min_sessions": 4, "max_sessions": 7},
            reward_bubbles=5,
            reward_description_template="Reward — attended {session_count} sessions this month ({amount} 🫧)",
            max_per_member_per_period=1,
            period=RewardPeriod.MONTH,
            category=RewardCategory.RETENTION,
            created_by="seed",
        ),
        RewardRule(
            id=uuid.UUID("00000000-0000-0000-0000-100000000006"),
            rule_name="monthly_attendance_8",
            display_name="Monthly Swim Streak (8+ sessions)",
            event_type="attendance.monthly_milestone",
            trigger_config={"min_sessions": 8},
            reward_bubbles=15,
            reward_description_template="Reward — attended {session_count} sessions this month ({amount} 🫧)",
            max_per_member_per_period=1,
            period=RewardPeriod.MONTH,
            replaces_rule_id=uuid.UUID("00000000-0000-0000-0000-100000000005"),
            priority=1,
            category=RewardCategory.RETENTION,
            created_by="seed",
        ),
        RewardRule(
            id=uuid.UUID("00000000-0000-0000-0000-100000000007"),
            rule_name="attendance_streak_4_weeks",
            display_name="4-Week Attendance Streak",
            event_type="attendance.streak",
            trigger_config={"min_consecutive_weeks": 4},
            reward_bubbles=20,
            reward_description_template="Reward — {streak_weeks}-week attendance streak! ({amount} 🫧)",
            category=RewardCategory.RETENTION,
            created_by="seed",
        ),
        RewardRule(
            id=uuid.UUID("00000000-0000-0000-0000-100000000008"),
            rule_name="member_reactivation",
            display_name="Welcome Back Bonus",
            event_type="member.reactivated",
            trigger_config={"min_inactive_days": 30},
            reward_bubbles=10,
            reward_description_template="Welcome back! Thanks for returning to SwimBuddz ({amount} 🫧)",
            max_per_member_per_period=1,
            period=RewardPeriod.YEAR,
            max_per_member_lifetime=2,
            category=RewardCategory.RETENTION,
            created_by="seed",
        ),
        RewardRule(
            id=uuid.UUID("00000000-0000-0000-0000-100000000009"),
            rule_name="membership_renewal",
            display_name="Membership Renewal Bonus",
            event_type="membership.renewed",
            trigger_config={},
            reward_bubbles=10,
            reward_description_template="Reward — membership renewed ({amount} 🫧)",
            max_per_member_per_period=1,
            period=RewardPeriod.YEAR,
            category=RewardCategory.RETENTION,
            created_by="seed",
        ),
        # --- COMMUNITY ---
        RewardRule(
            id=uuid.UUID("00000000-0000-0000-0000-100000000010"),
            rule_name="volunteer_event",
            display_name="Event Volunteer Reward",
            event_type="volunteer.completed",
            trigger_config={},
            reward_bubbles=20,
            reward_description_template="Volunteer reward — {event_name} ({amount} 🫧)",
            requires_admin_confirmation=True,
            category=RewardCategory.COMMUNITY,
            created_by="seed",
        ),
        RewardRule(
            id=uuid.UUID("00000000-0000-0000-0000-100000000011"),
            rule_name="peer_coaching",
            display_name="Peer Coaching Reward",
            event_type="volunteer.peer_coaching",
            trigger_config={},
            reward_bubbles=10,
            reward_description_template="Reward — peer coaching session ({amount} 🫧)",
            max_per_member_per_period=4,
            period=RewardPeriod.MONTH,
            requires_admin_confirmation=True,
            category=RewardCategory.COMMUNITY,
            created_by="seed",
        ),
        RewardRule(
            id=uuid.UUID("00000000-0000-0000-0000-100000000012"),
            rule_name="content_published",
            display_name="Content Contribution Reward",
            event_type="content.published",
            trigger_config={},
            reward_bubbles=5,
            reward_description_template='Reward — published "{post_title}" ({amount} 🫧)',
            max_per_member_per_period=4,
            period=RewardPeriod.MONTH,
            category=RewardCategory.COMMUNITY,
            created_by="seed",
        ),
        RewardRule(
            id=uuid.UUID("00000000-0000-0000-0000-100000000013"),
            rule_name="rideshare_driver",
            display_name="Ride-Share Driver Reward",
            event_type="transport.ride_completed",
            trigger_config={},
            reward_bubbles=3,
            reward_description_template="Reward — ride share to {pool_name}, {passenger_count} passengers ({amount} 🫧)",
            max_per_member_per_period=20,
            period=RewardPeriod.MONTH,
            category=RewardCategory.COMMUNITY,
            created_by="seed",
        ),
        RewardRule(
            id=uuid.UUID("00000000-0000-0000-0000-100000000014"),
            rule_name="safety_report",
            display_name="Safety Report Reward",
            event_type="safety.report_confirmed",
            trigger_config={},
            reward_bubbles=5,
            reward_description_template="Reward — safety concern reported and confirmed ({amount} 🫧)",
            requires_admin_confirmation=True,
            category=RewardCategory.COMMUNITY,
            created_by="seed",
        ),
        # --- SPENDING ---
        RewardRule(
            id=uuid.UUID("00000000-0000-0000-0000-100000000015"),
            rule_name="first_topup",
            display_name="First Topup Bonus",
            event_type="topup.first",
            trigger_config={},
            reward_bubbles=10,
            reward_description_template="First topup bonus — welcome to Bubbles! ({amount} 🫧)",
            max_per_member_lifetime=1,
            category=RewardCategory.SPENDING,
            created_by="seed",
        ),
        RewardRule(
            id=uuid.UUID("00000000-0000-0000-0000-100000000016"),
            rule_name="large_topup",
            display_name="Large Topup Bonus",
            event_type="topup.completed",
            trigger_config={"min_amount": 200},
            reward_bubbles=5,
            reward_description_template="Reward — large topup bonus ({amount} 🫧)",
            max_per_member_per_period=1,
            period=RewardPeriod.MONTH,
            category=RewardCategory.SPENDING,
            created_by="seed",
        ),
        RewardRule(
            id=uuid.UUID("00000000-0000-0000-0000-100000000017"),
            rule_name="first_store_purchase",
            display_name="First Store Purchase Bonus",
            event_type="store.first_purchase",
            trigger_config={},
            reward_bubbles=3,
            reward_description_template="Reward — first store purchase ({amount} 🫧)",
            max_per_member_lifetime=1,
            category=RewardCategory.SPENDING,
            created_by="seed",
        ),
        RewardRule(
            id=uuid.UUID("00000000-0000-0000-0000-100000000018"),
            rule_name="tier_upgrade",
            display_name="Tier Upgrade Bonus",
            event_type="membership.upgraded",
            trigger_config={},
            reward_bubbles=15,
            reward_description_template="Reward — upgraded to {new_tier} ({amount} 🫧)",
            category=RewardCategory.SPENDING,
            created_by="seed",
        ),
        # --- ACADEMY ---
        RewardRule(
            id=uuid.UUID("00000000-0000-0000-0000-100000000019"),
            rule_name="academy_graduation",
            display_name="Academy Graduation Reward",
            event_type="academy.graduated",
            trigger_config={},
            reward_bubbles=25,
            reward_description_template="Congratulations! Graduated from {program_name} ({amount} 🫧)",
            category=RewardCategory.ACADEMY,
            created_by="seed",
        ),
        RewardRule(
            id=uuid.UUID("00000000-0000-0000-0000-100000000020"),
            rule_name="academy_milestone",
            display_name="Academy Skill Milestone",
            event_type="academy.milestone_passed",
            trigger_config={},
            reward_bubbles=5,
            reward_description_template="Milestone — {milestone_name} achieved ({amount} 🫧)",
            category=RewardCategory.ACADEMY,
            created_by="seed",
        ),
        RewardRule(
            id=uuid.UUID("00000000-0000-0000-0000-100000000021"),
            rule_name="academy_perfect_attendance",
            display_name="Academy Perfect Attendance",
            event_type="academy.perfect_attendance",
            trigger_config={},
            reward_bubbles=15,
            reward_description_template="Perfect attendance — {program_name}, {cohort_name} ({amount} 🫧)",
            category=RewardCategory.ACADEMY,
            created_by="seed",
        ),
    ]


async def seed_reward_rules():
    """Insert default reward rules if none exist. Idempotent."""
    async with AsyncSessionLocal() as session:
        async with session.begin():
            # Check if rules already exist
            stmt = select(func.count()).select_from(RewardRule)
            result = await session.execute(stmt)
            rule_count = result.scalar_one()

            if rule_count > 0:
                print(f"  Reward rules already exist ({rule_count}), skipping...")
                return

            rules = build_default_rules()
            for rule in rules:
                session.add(rule)
            await session.flush()

            print(f"  ✓ Seeded {len(rules)} default reward rules")


if __name__ == "__main__":
    print("Seeding reward rules...")
    asyncio.run(seed_reward_rules())
