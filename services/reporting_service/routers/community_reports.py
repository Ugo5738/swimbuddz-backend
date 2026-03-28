"""Community-facing quarterly report endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import get_current_user
from libs.auth.models import AuthUser
from libs.common.logging import get_logger
from libs.db.session import get_async_db
from services.reporting_service.models import (
    CommunityQuarterlyStats,
    LeaderboardCategory,
    MemberQuarterlyReport,
)
from services.reporting_service.schemas.reports import (
    CommunityQuarterlyStatsResponse,
    LeaderboardEntry,
    LeaderboardResponse,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/reports/community", tags=["community-reports"])

# Map leaderboard categories to model fields
LEADERBOARD_FIELDS = {
    LeaderboardCategory.ATTENDANCE: MemberQuarterlyReport.total_sessions_attended,
    LeaderboardCategory.STREAKS: MemberQuarterlyReport.streak_longest,
    LeaderboardCategory.MILESTONES: MemberQuarterlyReport.milestones_achieved,
    LeaderboardCategory.VOLUNTEER_HOURS: MemberQuarterlyReport.volunteer_hours,
    LeaderboardCategory.BUBBLES_EARNED: MemberQuarterlyReport.bubbles_earned,
}


@router.get("/quarterly", response_model=CommunityQuarterlyStatsResponse)
async def get_community_quarterly_stats(
    year: int = Query(..., ge=2025, le=2030),
    quarter: int = Query(..., ge=1, le=4),
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Get community-wide quarterly stats."""
    result = await db.execute(
        select(CommunityQuarterlyStats).where(
            CommunityQuarterlyStats.year == year,
            CommunityQuarterlyStats.quarter == quarter,
        )
    )
    stats = result.scalar_one_or_none()

    if stats is None:
        raise HTTPException(
            status_code=404, detail="Community stats not available for this quarter."
        )

    return stats


@router.get("/leaderboards", response_model=LeaderboardResponse)
async def get_leaderboard(
    year: int = Query(..., ge=2025, le=2030),
    quarter: int = Query(..., ge=1, le=4),
    category: LeaderboardCategory = Query(LeaderboardCategory.ATTENDANCE),
    limit: int = Query(20, ge=5, le=50),
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Get leaderboard for a specific category, excluding opted-out members."""
    field = LEADERBOARD_FIELDS.get(category)
    if field is None:
        raise HTTPException(status_code=400, detail=f"Unknown category: {category}")

    result = await db.execute(
        select(MemberQuarterlyReport)
        .where(
            MemberQuarterlyReport.year == year,
            MemberQuarterlyReport.quarter == quarter,
            MemberQuarterlyReport.leaderboard_opt_out == False,  # noqa: E712
        )
        .order_by(field.desc())
        .limit(limit)
    )
    reports = result.scalars().all()

    entries = [
        LeaderboardEntry(
            rank=idx + 1,
            member_id=r.member_id,
            member_name=r.member_name,
            value=float(getattr(r, field.key)),
            is_current_user=(str(r.member_auth_id) == current_user.user_id),
        )
        for idx, r in enumerate(reports)
    ]

    return LeaderboardResponse(
        category=category.value,
        year=year,
        quarter=quarter,
        entries=entries,
    )
