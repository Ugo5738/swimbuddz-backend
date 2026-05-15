"""Admin: dashboard summary + reliability report."""

from datetime import date, datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends
from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.common.member_utils import resolve_members_basic
from libs.db.session import get_async_db
from services.volunteer_service.models import (
    OpportunityStatus,
    SlotStatus,
    VolunteerHoursLog,
    VolunteerOpportunity,
    VolunteerProfile,
    VolunteerSlot,
)
from services.volunteer_service.schemas import (
    LeaderboardEntry,
    VolunteerDashboardSummary,
    VolunteerProfileResponse,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()


@router.get("/dashboard", response_model=VolunteerDashboardSummary)
async def admin_dashboard(
    admin: Annotated[AuthUser, Depends(require_admin)],
    db: AsyncSession = Depends(get_async_db),
):
    # Total active volunteers
    total_active = (
        await db.execute(
            select(func.count(VolunteerProfile.id)).where(
                VolunteerProfile.is_active.is_(True)
            )
        )
    ).scalar() or 0

    # Hours this month
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    hours_this_month = (
        await db.execute(
            select(func.coalesce(func.sum(VolunteerHoursLog.hours), 0.0)).where(
                VolunteerHoursLog.created_at >= month_start
            )
        )
    ).scalar() or 0.0

    # Upcoming opportunities (next 14 days)
    today = date.today()
    upcoming = (
        await db.execute(
            select(func.count(VolunteerOpportunity.id)).where(
                VolunteerOpportunity.date >= today,
                VolunteerOpportunity.date <= today + timedelta(days=14),
                VolunteerOpportunity.status.in_(
                    [
                        OpportunityStatus.OPEN,
                        OpportunityStatus.IN_PROGRESS,
                    ]
                ),
            )
        )
    ).scalar() or 0

    # Unfilled slots
    unfilled = (
        await db.execute(
            select(
                func.coalesce(
                    func.sum(
                        VolunteerOpportunity.slots_needed
                        - VolunteerOpportunity.slots_filled
                    ),
                    0,
                )
            ).where(
                VolunteerOpportunity.status == OpportunityStatus.OPEN,
                VolunteerOpportunity.date >= today,
            )
        )
    ).scalar() or 0

    # No-show rate
    total_completed = (
        await db.execute(
            select(func.count(VolunteerSlot.id)).where(
                VolunteerSlot.status == SlotStatus.COMPLETED
            )
        )
    ).scalar() or 0
    total_no_shows = (
        await db.execute(
            select(func.count(VolunteerSlot.id)).where(
                VolunteerSlot.status == SlotStatus.NO_SHOW
            )
        )
    ).scalar() or 0
    total_events = total_completed + total_no_shows
    no_show_rate = (total_no_shows / total_events * 100) if total_events > 0 else 0.0

    # Top 5 volunteers
    top_rows = (
        (
            await db.execute(
                select(VolunteerProfile)
                .where(VolunteerProfile.is_active.is_(True))
                .order_by(VolunteerProfile.total_hours.desc())
                .limit(5)
            )
        )
        .scalars()
        .all()
    )

    # Batch-resolve member names via HTTP
    top_member_ids = [p.member_id for p in top_rows]
    member_map = await resolve_members_basic(top_member_ids) if top_member_ids else {}

    top_volunteers = []
    for rank, p in enumerate(top_rows, 1):
        info = member_map.get(str(p.member_id))
        top_volunteers.append(
            LeaderboardEntry(
                rank=rank,
                member_id=p.member_id,
                member_name=info.full_name if info else None,
                total_hours=p.total_hours,
                total_sessions=p.total_sessions_volunteered,
                recognition_tier=p.recognition_tier,
            )
        )

    return VolunteerDashboardSummary(
        total_active_volunteers=total_active,
        total_hours_this_month=float(hours_this_month),
        upcoming_opportunities=upcoming,
        unfilled_slots=unfilled,
        no_show_rate=round(no_show_rate, 1),
        top_volunteers=top_volunteers,
    )


@router.get("/reliability-report", response_model=list[VolunteerProfileResponse])
async def reliability_report(
    admin: Annotated[AuthUser, Depends(require_admin)],
    db: AsyncSession = Depends(get_async_db),
):
    """Volunteer reliability report, sorted ascending (worst first)."""
    profiles = (
        (
            await db.execute(
                select(VolunteerProfile)
                .where(VolunteerProfile.is_active.is_(True))
                .order_by(VolunteerProfile.reliability_score.asc())
            )
        )
        .scalars()
        .all()
    )

    # Batch-resolve member names via HTTP
    member_ids = [p.member_id for p in profiles]
    member_map = await resolve_members_basic(member_ids) if member_ids else {}

    results = []
    for p in profiles:
        data = {c.key: getattr(p, c.key) for c in p.__table__.columns}
        info = member_map.get(str(p.member_id))
        data["member_name"] = info.full_name if info else None
        data["member_email"] = info.email if info else None
        results.append(data)
    return results
