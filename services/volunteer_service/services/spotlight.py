"""Volunteer spotlight selection and monthly feature rotation."""

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.datetime_utils import utc_now
from services.volunteer_service.models import VolunteerHoursLog, VolunteerProfile


@dataclass(frozen=True)
class VolunteerOfMonthResult:
    """Result of applying the monthly volunteer spotlight rotation."""

    period_start: date
    period_end: date
    featured_until: datetime
    member_id: uuid.UUID | None
    monthly_hours: float
    monthly_logs: int


def _next_month_start(value: date) -> date:
    if value.month == 12:
        return date(value.year + 1, 1, 1)
    return date(value.year, value.month + 1, 1)


def _previous_month_bounds(now: datetime) -> tuple[date, date]:
    current_month = date(now.year, now.month, 1)
    if current_month.month == 1:
        previous_month = date(current_month.year - 1, 12, 1)
    else:
        previous_month = date(current_month.year, current_month.month - 1, 1)
    return previous_month, current_month


def _display_until(now: datetime) -> datetime:
    """Return the exclusive expiry timestamp for the current display month."""
    current_month = date(now.year, now.month, 1)
    next_month = _next_month_start(current_month)
    return datetime(next_month.year, next_month.month, 1, tzinfo=timezone.utc)


async def select_volunteer_of_month(
    db: AsyncSession,
    *,
    period_start: date,
    period_end: date,
) -> tuple[VolunteerProfile, float, int] | None:
    """Select the active volunteer with the most logged hours in a period.

    Ties are resolved by number of logs, then all-time hours, then member_id for
    deterministic results.
    """
    monthly_hours = func.coalesce(func.sum(VolunteerHoursLog.hours), 0.0).label(
        "monthly_hours"
    )
    monthly_logs = func.count(VolunteerHoursLog.id).label("monthly_logs")

    row = (
        await db.execute(
            select(VolunteerProfile, monthly_hours, monthly_logs)
            .join(
                VolunteerHoursLog,
                VolunteerHoursLog.member_id == VolunteerProfile.member_id,
            )
            .where(
                VolunteerProfile.is_active.is_(True),
                VolunteerHoursLog.date >= period_start,
                VolunteerHoursLog.date < period_end,
                VolunteerHoursLog.hours > 0,
            )
            .group_by(VolunteerProfile.id)
            .order_by(
                desc(monthly_hours),
                desc(monthly_logs),
                VolunteerProfile.total_hours.desc(),
                VolunteerProfile.member_id.asc(),
            )
            .limit(1)
        )
    ).one_or_none()

    if row is None:
        return None

    profile, hours, logs = row
    return profile, float(hours or 0.0), int(logs or 0)


async def apply_monthly_volunteer_spotlight(
    db: AsyncSession,
    *,
    now: datetime | None = None,
    period_start: date | None = None,
    period_end: date | None = None,
) -> VolunteerOfMonthResult:
    """Feature the previous month's top volunteer for the current month.

    The monthly cron runs just after a month closes. It evaluates the closed
    month, then keeps the winner visible until the end of the current display
    month. If no one logged hours in the closed month, no volunteer is featured.
    """
    now = now or utc_now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    if period_start is None or period_end is None:
        period_start, period_end = _previous_month_bounds(now)

    featured_until = _display_until(now)
    winner = await select_volunteer_of_month(
        db,
        period_start=period_start,
        period_end=period_end,
    )

    current_featured = (
        (
            await db.execute(
                select(VolunteerProfile).where(VolunteerProfile.is_featured.is_(True))
            )
        )
        .scalars()
        .all()
    )

    if winner is None:
        for profile in current_featured:
            profile.is_featured = False
        await db.commit()
        return VolunteerOfMonthResult(
            period_start=period_start,
            period_end=period_end,
            featured_until=featured_until,
            member_id=None,
            monthly_hours=0.0,
            monthly_logs=0,
        )

    winner_profile, monthly_hours_value, monthly_logs_value = winner
    for profile in current_featured:
        if profile.member_id != winner_profile.member_id:
            profile.is_featured = False

    winner_profile.is_featured = True
    winner_profile.featured_from = now
    winner_profile.featured_until = featured_until

    await db.commit()

    return VolunteerOfMonthResult(
        period_start=period_start,
        period_end=period_end,
        featured_until=featured_until,
        member_id=winner_profile.member_id,
        monthly_hours=monthly_hours_value,
        monthly_logs=monthly_logs_value,
    )


__all__ = [
    "VolunteerOfMonthResult",
    "apply_monthly_volunteer_spotlight",
    "select_volunteer_of_month",
]
