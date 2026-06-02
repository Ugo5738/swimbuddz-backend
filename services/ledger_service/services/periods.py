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

from libs.common.datetime_utils import utc_now
from services.ledger_service.models import AuditLog, Period
from services.ledger_service.models.enums import (
    AuditActionType,
    PeriodStatus,
    PeriodType,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

# Legal period status transitions (design §10.2). Hard-close is final-ish; a
# hard -> soft reopen exists as an owner break-glass (gated in the route).
ALLOWED_TRANSITIONS: set[tuple[PeriodStatus, PeriodStatus]] = {
    (PeriodStatus.OPEN, PeriodStatus.SOFT_CLOSED),
    (PeriodStatus.SOFT_CLOSED, PeriodStatus.OPEN),
    (PeriodStatus.SOFT_CLOSED, PeriodStatus.HARD_CLOSED),
    (PeriodStatus.HARD_CLOSED, PeriodStatus.SOFT_CLOSED),
}


class InvalidTransitionError(Exception):
    """The requested period status transition isn't allowed."""


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


async def transition_period(
    session: AsyncSession,
    org_id: uuid.UUID,
    period_id: uuid.UUID,
    to_status: PeriodStatus,
    actor_id: uuid.UUID,
) -> Period:
    """Move a period to `to_status` if the transition is legal. Audited.

    Idempotent if already in `to_status`. Raises InvalidTransitionError if the
    period is missing or the (from, to) pair isn't allowed. Caller commits.
    """
    period = await session.get(Period, period_id)
    if period is None or period.org_id != org_id:
        raise InvalidTransitionError("period not found")
    if period.status == to_status:
        return period
    if (period.status, to_status) not in ALLOWED_TRANSITIONS:
        raise InvalidTransitionError(
            f"cannot move {period.period_name} "
            f"from {period.status.value} to {to_status.value}"
        )
    old = period.status
    period.status = to_status
    if to_status in (PeriodStatus.SOFT_CLOSED, PeriodStatus.HARD_CLOSED):
        period.closed_at = utc_now()
        period.closed_by_user_id = actor_id
    else:
        period.closed_at = None
        period.closed_by_user_id = None
    session.add(
        AuditLog(
            org_id=org_id,
            actor_user_id=actor_id,
            action=AuditActionType.PERIOD_CLOSED,
            subject_type="period",
            subject_id=str(period.id),
            payload={
                "from": old.value,
                "to": to_status.value,
                "period": period.period_name,
            },
        )
    )
    await session.flush()
    return period
