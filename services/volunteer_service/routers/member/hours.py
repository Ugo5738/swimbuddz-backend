"""Hours-history, hours-summary, and leaderboard endpoints."""

import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from libs.auth.dependencies import get_current_user
from libs.auth.models import AuthUser
from libs.common.member_utils import resolve_members_basic
from libs.common.service_client import get_member_by_auth_id
from libs.common.datetime_utils import utc_now
from libs.db.session import get_async_db
from services.volunteer_service.models import (
    VolunteerHoursLog,
    VolunteerProfile,
    VolunteerRole,
)
from services.volunteer_service.schemas import (
    HoursSummaryResponse,
    LeaderboardEntry,
    VolunteerHoursLogResponse,
)
from services.volunteer_service.services import next_recognition_hours_needed
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()


@router.get("/hours/me", response_model=list[VolunteerHoursLogResponse])
async def my_hours(
    user: Annotated[AuthUser, Depends(get_current_user)],
    skip: int = 0,
    limit: int = 50,
    db: AsyncSession = Depends(get_async_db),
):
    """Get my hours history."""
    member = await get_member_by_auth_id(user.user_id, calling_service="volunteer")
    if not member:
        raise HTTPException(status_code=404, detail="Member profile not found")
    member_id = uuid.UUID(member["id"])
    rows = (
        (
            await db.execute(
                select(VolunteerHoursLog)
                .where(VolunteerHoursLog.member_id == member_id)
                .order_by(VolunteerHoursLog.date.desc())
                .offset(skip)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return rows


@router.get("/hours/me/summary", response_model=HoursSummaryResponse)
async def my_hours_summary(
    user: Annotated[AuthUser, Depends(get_current_user)],
    db: AsyncSession = Depends(get_async_db),
):
    """Get my hours summary with tier info."""
    member = await get_member_by_auth_id(user.user_id, calling_service="volunteer")
    if not member:
        raise HTTPException(status_code=404, detail="Member profile not found")
    member_id = uuid.UUID(member["id"])
    profile = (
        await db.execute(
            select(VolunteerProfile).where(VolunteerProfile.member_id == member_id)
        )
    ).scalar_one_or_none()
    if not profile:
        raise HTTPException(status_code=404, detail="Volunteer profile not found")

    # Hours this month
    now = utc_now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    hours_this_month = (
        await db.execute(
            select(func.coalesce(func.sum(VolunteerHoursLog.hours), 0.0)).where(
                VolunteerHoursLog.member_id == member_id,
                VolunteerHoursLog.created_at >= month_start,
            )
        )
    ).scalar() or 0.0

    # Hours by role
    by_role_rows = (
        await db.execute(
            select(
                VolunteerHoursLog.role_id,
                func.sum(VolunteerHoursLog.hours).label("hours"),
                func.count(VolunteerHoursLog.id).label("count"),
            )
            .where(VolunteerHoursLog.member_id == member_id)
            .group_by(VolunteerHoursLog.role_id)
        )
    ).all()

    by_role = []
    for row in by_role_rows:
        role_id, hours, count = row
        role_name = None
        if role_id:
            role = (
                await db.execute(
                    select(VolunteerRole).where(VolunteerRole.id == role_id)
                )
            ).scalar_one_or_none()
            role_name = role.title if role else None
        by_role.append(
            {
                "role_id": str(role_id) if role_id else None,
                "role_name": role_name,
                "hours": float(hours),
                "sessions": count,
            }
        )

    return HoursSummaryResponse(
        total_hours=profile.total_hours,
        total_sessions=profile.total_sessions_volunteered,
        hours_this_month=float(hours_this_month),
        tier=profile.tier,
        recognition_tier=profile.recognition_tier,
        reliability_score=profile.reliability_score,
        next_tier_hours_needed=next_recognition_hours_needed(profile.total_hours),
        by_role=by_role,
    )


@router.get("/hours/leaderboard", response_model=list[LeaderboardEntry])
async def leaderboard(
    period: str = Query("all_time", regex="^(all_time|this_month)$"),
    limit: int = Query(20, le=100),
    db: AsyncSession = Depends(get_async_db),
):
    """Top volunteers by hours."""
    if period == "this_month":
        now = utc_now()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        q = (
            select(
                VolunteerHoursLog.member_id,
                func.sum(VolunteerHoursLog.hours).label("total_hours"),
                func.count(VolunteerHoursLog.id).label("total_sessions"),
            )
            .where(VolunteerHoursLog.created_at >= month_start)
            .group_by(VolunteerHoursLog.member_id)
            .order_by(func.sum(VolunteerHoursLog.hours).desc())
            .limit(limit)
        )
    else:
        q = (
            select(
                VolunteerProfile.member_id,
                VolunteerProfile.total_hours,
                VolunteerProfile.total_sessions_volunteered.label("total_sessions"),
            )
            .where(VolunteerProfile.is_active.is_(True))
            .order_by(VolunteerProfile.total_hours.desc())
            .limit(limit)
        )

    rows = (await db.execute(q)).all()

    # Batch-resolve member names + payment status via HTTP
    all_member_ids = [row[0] for row in rows]
    member_map = await resolve_members_basic(all_member_ids) if all_member_ids else {}

    now = utc_now()

    def _is_paid(info) -> bool:
        if not info or not info.community_paid_until:
            return False
        try:
            paid_until = datetime.fromisoformat(info.community_paid_until)
            if paid_until.tzinfo is None:
                paid_until = paid_until.replace(tzinfo=timezone.utc)
            return paid_until > now
        except (ValueError, TypeError):
            return False

    results = []
    for row in rows:
        member_id = row[0]
        info = member_map.get(str(member_id))
        if not _is_paid(info):
            continue

        # Get recognition tier
        profile = (
            await db.execute(
                select(VolunteerProfile.recognition_tier).where(
                    VolunteerProfile.member_id == member_id
                )
            )
        ).scalar_one_or_none()

        results.append(
            LeaderboardEntry(
                rank=len(results) + 1,
                member_id=member_id,
                member_name=info.full_name if info else None,
                total_hours=float(row[1]),
                total_sessions=int(row[2]),
                recognition_tier=profile,
            )
        )
    return results
