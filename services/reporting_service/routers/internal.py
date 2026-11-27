"""Internal service-to-service reporting endpoints.

These endpoints are called by background workers or other services,
not by frontend clients directly.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_service_role
from libs.auth.models import AuthUser
from libs.common.logging import get_logger
from libs.db.session import get_async_db
from services.reporting_service.services.aggregator import (
    compute_all_member_reports,
    compute_community_stats,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/internal/reporting", tags=["internal"])


@router.post("/generate-snapshot")
async def trigger_snapshot_generation(
    year: int,
    quarter: int,
    caller: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Trigger snapshot generation for a quarter (called by ARQ worker)."""
    logger.info(f"Internal snapshot generation triggered for Q{quarter} {year}")
    count = await compute_all_member_reports(year, quarter, db)
    await compute_community_stats(year, quarter, db)
    return {"status": "completed", "member_count": count}


@router.get("/members/{member_auth_id}/scorecard-summary")
async def get_member_reporting_scorecard_summary(
    member_auth_id: str,
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Aggregate lifetime consistency & community stats from quarterly snapshots (ADR-026)."""
    from sqlalchemy import select

    from services.reporting_service.models import MemberQuarterlyReport
    from services.reporting_service.services.quarter_utils import quarter_label

    reports = (
        (
            await db.execute(
                select(MemberQuarterlyReport)
                .where(MemberQuarterlyReport.member_auth_id == member_auth_id)
                .order_by(
                    MemberQuarterlyReport.year.desc(),
                    MemberQuarterlyReport.quarter.desc(),
                )
            )
        )
        .scalars()
        .all()
    )

    if not reports:
        return {
            "total_sessions_attended": 0,
            "total_pool_hours": 0.0,
            "longest_streak_weeks": 0,
            "current_streak_weeks": 0,
            "punctuality_rate": None,
            "volunteer_hours": 0.0,
            "rides_shared": 0,
            "events_attended": 0,
            "latest_quarter_label": None,
        }

    total_sessions = 0
    total_pool_hours = 0.0
    longest_streak = 0
    volunteer_hours = 0.0
    rides_shared = 0
    events_attended = 0
    punctuality_values: list[float] = []

    for r in reports:
        all_swims = None
        if isinstance(r.activity_details, dict):
            att = r.activity_details.get("attendance")
            if isinstance(att, dict) and att.get("all_sessions_attended") is not None:
                all_swims = int(att["all_sessions_attended"])
        total_sessions += (
            all_swims if all_swims is not None else int(r.total_sessions_attended or 0)
        )
        total_pool_hours += float(r.pool_hours or 0.0)
        longest_streak = max(longest_streak, int(r.streak_longest or 0))
        volunteer_hours += float(r.volunteer_hours or 0.0)
        rides_shared += int(r.rides_taken or 0) + int(r.rides_offered or 0)
        events_attended += int(r.events_attended or 0)
        if r.total_sessions_attended and r.punctuality_rate is not None:
            punctuality_values.append(float(r.punctuality_rate))

    latest = reports[0]
    avg_punctuality = (
        round(sum(punctuality_values) / len(punctuality_values), 4)
        if punctuality_values
        else None
    )

    return {
        "total_sessions_attended": total_sessions,
        "total_pool_hours": round(total_pool_hours, 1),
        "longest_streak_weeks": longest_streak,
        "current_streak_weeks": int(latest.streak_current or 0),
        "punctuality_rate": avg_punctuality,
        "volunteer_hours": round(volunteer_hours, 1),
        "rides_shared": rides_shared,
        "events_attended": events_attended,
        "latest_quarter_label": quarter_label(latest.year, latest.quarter),
    }
