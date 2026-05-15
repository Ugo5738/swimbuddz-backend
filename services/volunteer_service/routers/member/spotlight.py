"""Public volunteer spotlight (featured + stats + milestones + top 5)."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from libs.common.member_utils import resolve_members_basic, resolve_members_with_photos
from libs.db.session import get_async_db
from services.volunteer_service.models import (
    RecognitionTier,
    VolunteerHoursLog,
    VolunteerProfile,
    VolunteerReward,
)
from services.volunteer_service.schemas import (
    LeaderboardEntry,
    SpotlightFeaturedVolunteer,
    SpotlightMilestone,
    SpotlightResponse,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()


@router.get("/spotlight", response_model=SpotlightResponse)
async def get_spotlight(db: AsyncSession = Depends(get_async_db)):
    """Public volunteer spotlight: featured volunteer, stats, milestones."""
    now = datetime.now(timezone.utc)

    def _is_paid(info) -> bool:
        """Check if member has active community payment."""
        if not info or not info.community_paid_until:
            return False
        try:
            paid_str = info.community_paid_until
            paid_until = datetime.fromisoformat(paid_str)
            if paid_until.tzinfo is None:
                paid_until = paid_until.replace(tzinfo=timezone.utc)
            return paid_until > now
        except (ValueError, TypeError):
            return False

    # 1. Featured volunteer
    featured_query = (
        select(VolunteerProfile)
        .where(
            VolunteerProfile.is_featured.is_(True),
            VolunteerProfile.is_active.is_(True),
        )
        .order_by(VolunteerProfile.featured_from.desc())
        .limit(1)
    )
    featured_profile = (await db.execute(featured_query)).scalar_one_or_none()

    featured = None
    if featured_profile:
        if (
            featured_profile.featured_until is None
            or featured_profile.featured_until > now
        ):
            member_info = await resolve_members_with_photos(
                [featured_profile.member_id]
            )
            info = member_info.get(str(featured_profile.member_id))
            if info and _is_paid(info):
                featured = SpotlightFeaturedVolunteer(
                    member_id=featured_profile.member_id,
                    member_name=info.full_name or "Volunteer",
                    profile_photo_url=info.profile_photo_url,
                    spotlight_quote=featured_profile.spotlight_quote,
                    recognition_tier=featured_profile.recognition_tier,
                    total_hours=featured_profile.total_hours,
                    preferred_roles=featured_profile.preferred_roles,
                )

    # 2. Aggregate stats
    total_active = (
        await db.execute(
            select(func.count(VolunteerProfile.id)).where(
                VolunteerProfile.is_active.is_(True)
            )
        )
    ).scalar() or 0

    total_hours = (
        await db.execute(select(func.coalesce(func.sum(VolunteerHoursLog.hours), 0.0)))
    ).scalar() or 0.0

    # 3. Milestones this month (recognition tier achievements)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    milestones: list[SpotlightMilestone] = []
    for tier in [RecognitionTier.BRONZE, RecognitionTier.SILVER, RecognitionTier.GOLD]:
        count = (
            await db.execute(
                select(func.count(VolunteerReward.id)).where(
                    VolunteerReward.trigger_type == "recognition_tier",
                    VolunteerReward.trigger_value == tier.value,
                    VolunteerReward.created_at >= month_start,
                )
            )
        ).scalar() or 0
        if count > 0:
            tier_label = tier.value.capitalize()
            milestones.append(
                SpotlightMilestone(
                    description=f"{count} volunteer{'s' if count > 1 else ''} reached {tier_label} tier",
                    count=count,
                )
            )

    # 4. Top leaderboard (fetch extra to allow payment filtering)
    top_rows = (
        (
            await db.execute(
                select(VolunteerProfile)
                .where(VolunteerProfile.is_active.is_(True))
                .order_by(VolunteerProfile.total_hours.desc())
                .limit(20)
            )
        )
        .scalars()
        .all()
    )

    # Resolve all member names + payment status via HTTP
    top_member_ids = [p.member_id for p in top_rows]
    member_map = await resolve_members_basic(top_member_ids) if top_member_ids else {}

    # Filter to only paid members
    top_volunteers = []
    for p in top_rows:
        info = member_map.get(str(p.member_id))
        if not _is_paid(info):
            continue
        top_volunteers.append(
            LeaderboardEntry(
                rank=len(top_volunteers) + 1,
                member_id=p.member_id,
                member_name=info.full_name,
                total_hours=p.total_hours,
                total_sessions=p.total_sessions_volunteered,
                recognition_tier=p.recognition_tier,
            )
        )
        if len(top_volunteers) >= 5:
            break

    return SpotlightResponse(
        featured_volunteer=featured,
        total_active_volunteers=total_active,
        total_hours_all_time=float(total_hours),
        milestones_this_month=milestones,
        top_volunteers=top_volunteers,
    )
