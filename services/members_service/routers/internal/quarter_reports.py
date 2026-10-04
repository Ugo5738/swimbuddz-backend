"""Quarter-level Club reporting endpoints for reporting_service."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_service_role
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from services.members_service.models import Club, ClubEnrollment

router = APIRouter()


class ClubQuarterSummary(BaseModel):
    active_members: int = 0
    new_enrollments: int = 0
    prior_period_members: int = 0
    retained_members: int = 0
    retention_rate: float = 0.0
    prepaid_enrollments: int = 0
    transition_enrollments: int = 0
    by_club: dict[str, dict] = Field(default_factory=dict)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@router.get("/club-quarter-summary", response_model=ClubQuarterSummary)
async def get_club_quarter_summary(
    date_from: datetime = Query(..., alias="from"),
    date_to: datetime = Query(..., alias="to"),
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Aggregate Club membership/retention for a reporting window.

    Retention compares members with an overlapping Club entitlement in the
    immediately preceding equal-length period against members overlapping the
    requested period.
    """
    date_from = _aware(date_from)
    date_to = _aware(date_to)
    period_length = date_to - date_from + timedelta(seconds=1)
    prev_from = date_from - period_length
    prev_to = date_from - timedelta(seconds=1)

    current_rows = (
        await db.execute(
            select(ClubEnrollment, Club)
            .join(Club, Club.id == ClubEnrollment.club_id)
            .where(
                ClubEnrollment.starts_at <= date_to,
                ClubEnrollment.ends_at >= date_from,
                ClubEnrollment.status == "active",
            )
        )
    ).all()

    prior_member_ids = set(
        (
            await db.execute(
                select(ClubEnrollment.member_id).where(
                    ClubEnrollment.starts_at <= prev_to,
                    ClubEnrollment.ends_at >= prev_from,
                    ClubEnrollment.status == "active",
                )
            )
        ).scalars().all()
    )
    current_member_ids = {row[0].member_id for row in current_rows}
    retained = current_member_ids & prior_member_ids

    new_enrollments = int(
        (
            await db.execute(
                select(func.count(ClubEnrollment.id)).where(
                    ClubEnrollment.created_at >= date_from,
                    ClubEnrollment.created_at <= date_to,
                )
            )
        ).scalar_one()
        or 0
    )

    by_club: dict[str, dict] = {}
    prepaid = 0
    transition = 0
    seen_per_club: dict[str, set] = {}
    for enrollment, club in current_rows:
        key = club.name
        bucket = by_club.setdefault(
            key,
            {
                "club_id": str(club.id),
                "location": club.location,
                "active_members": 0,
                "prepaid_enrollments": 0,
                "transition_enrollments": 0,
            },
        )
        seen = seen_per_club.setdefault(key, set())
        seen.add(enrollment.member_id)
        if enrollment.payment_mode == "quarterly_prepaid":
            prepaid += 1
            bucket["prepaid_enrollments"] += 1
        elif enrollment.payment_mode == "transition_per_session":
            transition += 1
            bucket["transition_enrollments"] += 1

    for key, seen in seen_per_club.items():
        by_club[key]["active_members"] = len(seen)

    return ClubQuarterSummary(
        active_members=len(current_member_ids),
        new_enrollments=new_enrollments,
        prior_period_members=len(prior_member_ids),
        retained_members=len(retained),
        retention_rate=round(len(retained) / len(prior_member_ids), 4)
        if prior_member_ids
        else 0.0,
        prepaid_enrollments=prepaid,
        transition_enrollments=transition,
        by_club=by_club,
    )
