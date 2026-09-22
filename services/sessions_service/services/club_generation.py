"""Recurring Club sessions use the same inherited costs and margin as Session edits."""

import calendar
import uuid
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from libs.common.config import get_settings
from libs.common.currency import naira_to_kobo
from libs.common.service_client import internal_post
from services.sessions_service.models import Session, SessionStatus
from services.sessions_service.schemas.templates import ClubTemplatePricing
from services.sessions_service.services.pricing import normalize_pricing_payload


def _month_index(value: date) -> int:
    return value.year * 12 + value.month - 1


def _nth_weekday(year: int, month: int, weekday: int, occurrence: int) -> int:
    last_day = calendar.monthrange(year, month)[1]
    if occurrence == -1:
        last = date(year, month, last_day)
        return last_day - ((last.weekday() - weekday) % 7)
    first = date(year, month, 1)
    candidate = 1 + ((weekday - first.weekday()) % 7) + (occurrence - 1) * 7
    return candidate if candidate <= last_day else -1


def _matches_month_day(template, candidate: date) -> bool:
    week_of_month = getattr(template, "week_of_month", None)
    if week_of_month is not None:
        return candidate.day == _nth_weekday(
            candidate.year,
            candidate.month,
            template.day_of_week,
            week_of_month,
        )
    requested_day = getattr(template, "day_of_month", None)
    if requested_day is None:
        requested_day = getattr(template, "starts_on", candidate).day
    target = min(requested_day, calendar.monthrange(candidate.year, candidate.month)[1])
    return candidate.day == target


def _matches_recurrence(template, candidate: date, anchor: date) -> bool:
    frequency = getattr(template, "frequency", None) or "weekly"
    interval = getattr(template, "interval", None) or 1
    if frequency == "weekly":
        week_index = (candidate - anchor).days // 7
        return (
            candidate.weekday() == template.day_of_week and week_index % interval == 0
        )

    months_since_start = _month_index(candidate) - _month_index(anchor)
    if months_since_start < 0:
        return False
    if frequency == "monthly":
        return months_since_start % interval == 0 and _matches_month_day(
            template, candidate
        )
    if frequency == "quarterly":
        return months_since_start % (3 * interval) == 0 and _matches_month_day(
            template, candidate
        )
    if frequency == "annual":
        target_month = getattr(template, "month_of_year", None) or anchor.month
        year_index = candidate.year - anchor.year
        return (
            year_index >= 0
            and year_index % interval == 0
            and candidate.month == target_month
            and _matches_month_day(template, candidate)
        )
    return False


def recurrence_dates(template, start, end, excluded=()):
    """Yield persisted template occurrences inside an inclusive date range."""

    starts_on = getattr(template, "starts_on", None) or start
    ends_on = getattr(template, "ends_on", None)
    current = max(start, starts_on)
    effective_end = min(end, ends_on) if ends_on else end
    excluded_dates = set(excluded)
    while current <= effective_end:
        if current not in excluded_dates and _matches_recurrence(
            template, current, starts_on
        ):
            yield current
        current += timedelta(days=1)


def club_instance_id(template_id, starts, pod_id):
    return uuid.uuid5(
        template_id, f"club:{starts.astimezone(timezone.utc).isoformat()}:{pod_id}"
    )


async def fresh_club_price(template, starts, ends):
    if not template.pool_id or not template.pricing_settings:
        raise HTTPException(
            422,
            "Configure the Club template's pool, expected attendance and margin first",
        )
    settings = ClubTemplatePricing.model_validate(template.pricing_settings)
    response = await internal_post(
        service_url=get_settings().POOLS_SERVICE_URL,
        path="/admin/pools/pricing/quote",
        calling_service="sessions",
        json={
            "pool_id": str(template.pool_id),
            "activity_scope": "club",
            "starts_at": starts.isoformat(),
            "ends_at": ends.isoformat(),
            "timezone": get_settings().TIMEZONE,
            "expected_attendees": settings.pricing_expected_attendees,
            "expected_staff": settings.expected_staff,
            "lanes": settings.lanes,
        },
    )
    if response.status_code >= 400:
        raise HTTPException(
            503, "Pool/operating rates are unavailable; nothing was published"
        )
    quote = response.json()
    if quote.get("currency") != "NGN" or quote.get("warnings"):
        raise HTTPException(
            422,
            "Configure effective NGN pool rates first: "
            + "; ".join(quote.get("warnings") or []),
        )
    # Only explicit ancillary lines persist. Effective inherited rates are
    # resolved anew for every date (including future rate changes).
    pricing = normalize_pricing_payload(
        {
            **settings.model_dump(mode="json"),
            "pricing_mode": "cost_plus",
            "cost_lines": quote["lines"]
            + [
                line.model_dump(mode="json")
                for line in settings.cost_lines
                if not line.source_rate_id
            ],
        }
    )
    pricing["pool_fee"] = naira_to_kobo(pricing["pool_fee"])
    return pricing


async def club_session_from_template(
    template, day, *, starts=None, mode=None, pod_id=None, capacity=None
):
    starts = starts or datetime.combine(
        day, template.start_time, tzinfo=ZoneInfo(get_settings().TIMEZONE)
    )
    ends = starts + timedelta(minutes=template.duration_minutes)
    pricing = await fresh_club_price(template, starts, ends)
    return Session(
        id=club_instance_id(template.id, starts, pod_id or template.pod_id),
        title=template.title,
        description=template.description,
        session_type=template.session_type,
        status=SessionStatus.DRAFT,
        club_id=template.club_id,
        club_access_mode=mode or template.club_access_mode,
        pool_id=template.pool_id,
        pod_id=pod_id or template.pod_id,
        location_name=template.location_name,
        capacity=capacity or template.capacity,
        ride_share_fee=getattr(template, "ride_share_fee", 0),
        starts_at=starts,
        ends_at=ends,
        timezone=get_settings().TIMEZONE,
        template_id=template.id,
        is_recurring_instance=True,
        **pricing,
    )
