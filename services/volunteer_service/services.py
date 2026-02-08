"""Business logic for the Volunteer Service."""

from datetime import datetime, timezone

from services.volunteer_service.models import (
    RecognitionTier,
    RewardType,
    SlotStatus,
    VolunteerHoursLog,
    VolunteerProfile,
    VolunteerReward,
    VolunteerSlot,
    VolunteerTier,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession


def compute_reliability_score(no_shows: int, late_cancellations: int) -> int:
    """Compute reliability score: 100 - (no_shows * 10) - (late_cancellations * 5), floor 0."""
    return max(0, 100 - (no_shows * 10) - (late_cancellations * 5))


def compute_tier(total_hours: float, reliability_score: int) -> VolunteerTier:
    """Compute volunteer tier based on hours and reliability."""
    if total_hours >= 50 and reliability_score >= 90:
        return VolunteerTier.TIER_3
    if total_hours >= 20 and reliability_score >= 80:
        return VolunteerTier.TIER_2
    return VolunteerTier.TIER_1


def compute_recognition(total_hours: float) -> RecognitionTier | None:
    """Compute recognition tier based on total hours."""
    if total_hours >= 100:
        return RecognitionTier.GOLD
    if total_hours >= 50:
        return RecognitionTier.SILVER
    if total_hours >= 10:
        return RecognitionTier.BRONZE
    return None


def next_recognition_hours_needed(total_hours: float) -> float | None:
    """Hours until next recognition tier."""
    if total_hours < 10:
        return 10 - total_hours
    if total_hours < 50:
        return 50 - total_hours
    if total_hours < 100:
        return 100 - total_hours
    return None


def is_late_cancellation(
    opportunity_date: datetime,
    opportunity_start_time,
    cancellation_deadline_hours: int,
) -> bool:
    """Check if cancellation is within the deadline window."""
    now = datetime.now(timezone.utc)
    if opportunity_start_time:
        opp_dt = datetime.combine(
            opportunity_date, opportunity_start_time, tzinfo=timezone.utc
        )
    else:
        opp_dt = datetime.combine(
            opportunity_date,
            datetime.min.time(),
            tzinfo=timezone.utc,
        )
    hours_until = (opp_dt - now).total_seconds() / 3600
    return hours_until < cancellation_deadline_hours


async def update_profile_aggregates(
    db: AsyncSession, member_id
) -> VolunteerProfile | None:
    """Recalculate all denormalized fields on a volunteer profile."""
    profile = (
        await db.execute(
            select(VolunteerProfile).where(VolunteerProfile.member_id == member_id)
        )
    ).scalar_one_or_none()

    if not profile:
        return None

    # Count completed slots
    completed_count = (
        await db.execute(
            select(func.count(VolunteerSlot.id)).where(
                VolunteerSlot.member_id == member_id,
                VolunteerSlot.status == SlotStatus.COMPLETED,
            )
        )
    ).scalar() or 0

    # Count no-shows
    no_show_count = (
        await db.execute(
            select(func.count(VolunteerSlot.id)).where(
                VolunteerSlot.member_id == member_id,
                VolunteerSlot.status == SlotStatus.NO_SHOW,
            )
        )
    ).scalar() or 0

    # Sum hours from log
    total_hours = (
        await db.execute(
            select(func.coalesce(func.sum(VolunteerHoursLog.hours), 0.0)).where(
                VolunteerHoursLog.member_id == member_id
            )
        )
    ).scalar() or 0.0

    profile.total_sessions_volunteered = completed_count
    profile.total_no_shows = no_show_count
    profile.total_hours = float(total_hours)
    profile.reliability_score = compute_reliability_score(
        profile.total_no_shows, profile.total_late_cancellations
    )

    # Compute tier (unless admin override)
    if profile.tier_override:
        profile.tier = profile.tier_override
    else:
        profile.tier = compute_tier(profile.total_hours, profile.reliability_score)

    # Recognition
    old_recognition = profile.recognition_tier
    new_recognition = compute_recognition(profile.total_hours)
    profile.recognition_tier = new_recognition

    # Auto-reward on recognition tier change
    if new_recognition and new_recognition != old_recognition:
        await _grant_recognition_reward(db, profile)

    return profile


async def _grant_recognition_reward(
    db: AsyncSession, profile: VolunteerProfile
) -> None:
    """Grant auto-reward when recognition tier changes."""
    tier = profile.recognition_tier
    if not tier:
        return

    reward_map = {
        RecognitionTier.BRONZE: (
            RewardType.PRIORITY_EVENT,
            "Bronze Volunteer Recognition",
            "Priority access to community events",
        ),
        RecognitionTier.SILVER: (
            RewardType.DISCOUNTED_SESSION,
            "Silver Volunteer Recognition",
            "20% discount on sessions",
        ),
        RecognitionTier.GOLD: (
            RewardType.MEMBERSHIP_DISCOUNT,
            "Gold Volunteer Recognition",
            "50% membership discount",
        ),
    }

    if tier not in reward_map:
        return

    reward_type, title, description = reward_map[tier]

    # Check if already granted
    existing = (
        await db.execute(
            select(VolunteerReward).where(
                VolunteerReward.member_id == profile.member_id,
                VolunteerReward.trigger_type == "recognition_tier",
                VolunteerReward.trigger_value == tier.value,
            )
        )
    ).scalar_one_or_none()

    if existing:
        return

    discount_map = {
        RecognitionTier.BRONZE: (None, None),
        RecognitionTier.SILVER: (20, None),
        RecognitionTier.GOLD: (50, None),
    }
    disc_pct, disc_ngn = discount_map.get(tier, (None, None))

    reward = VolunteerReward(
        member_id=profile.member_id,
        reward_type=reward_type,
        title=title,
        description=description,
        trigger_type="recognition_tier",
        trigger_value=tier.value,
        discount_percent=disc_pct,
        discount_amount_ngn=disc_ngn,
    )
    db.add(reward)
