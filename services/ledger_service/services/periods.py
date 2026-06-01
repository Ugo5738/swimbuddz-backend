"""Accounting-period resolution.

Posting resolves the month-period containing an entry's date, auto-creating it
(open) on first use. Period close is a later phase; PR-2 only needs
resolve-or-create. Concurrency on first-entry-of-a-new-month is handled with a
savepoint + re-select so two simultaneous posts don't both fail on the unique
(org_id, period_name) constraint.
"""

from __future__ import annotations

import calendar
import uuid
from datetime import date

from services.ledger_service.models import Period
from services.ledger_service.models.enums import PeriodStatus, PeriodType
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession


def month_bounds(d: date) -> tuple[str, date, date]:
    """Return (period_name 'YYYY-MM', first_day, last_day) for d's month."""
    name = f"{d.year:04d}-{d.month:02d}"
    last_day = calendar.monthrange(d.year, d.month)[1]
    return name, date(d.year, d.month, 1), date(d.year, d.month, last_day)


async def _select_period(
    session: AsyncSession, org_id: uuid.UUID, name: str
) -> Period | None:
    return (
        await session.execute(
            select(Period).where(Period.org_id == org_id, Period.period_name == name)
        )
    ).scalar_one_or_none()


async def resolve_or_create_period(
    session: AsyncSession, org_id: uuid.UUID, entry_date: date
) -> Period:
    """Return the month-period containing entry_date, creating it open if absent."""
    name, start, end = month_bounds(entry_date)
    period = await _select_period(session, org_id, name)
    if period is not None:
        return period

    period = Period(
        org_id=org_id,
        period_name=name,
        period_type=PeriodType.MONTH,
        start_date=start,
        end_date=end,
        status=PeriodStatus.OPEN,
    )
    try:
        async with session.begin_nested():
            session.add(period)
            await session.flush()
    except IntegrityError:
        # A concurrent post created the same period first — use that one.
        period = await _select_period(session, org_id, name)
        if period is None:
            raise
    return period
