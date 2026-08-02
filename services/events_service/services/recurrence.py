"""Pure recurrence helpers for admin event templates."""

import calendar
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from services.events_service.models import EventTemplate
from services.events_service.schemas.planning import EventOccurrence


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


def _matches_month_day(template: EventTemplate, candidate: date) -> bool:
    if template.week_of_month is not None and template.day_of_week is not None:
        target = _nth_weekday(
            candidate.year,
            candidate.month,
            template.day_of_week,
            template.week_of_month,
        )
        return candidate.day == target
    requested_day = template.day_of_month or template.starts_on.day
    target = min(requested_day, calendar.monthrange(candidate.year, candidate.month)[1])
    return candidate.day == target


def _matches(template: EventTemplate, candidate: date) -> bool:
    if candidate < template.starts_on:
        return False
    if template.ends_on and candidate > template.ends_on:
        return False

    if template.frequency == "weekly":
        week_index = (candidate - template.starts_on).days // 7
        return (
            candidate.weekday() == template.day_of_week
            and week_index % template.interval == 0
        )

    months_since_start = _month_index(candidate) - _month_index(template.starts_on)
    if months_since_start < 0:
        return False
    if template.frequency == "monthly":
        return months_since_start % template.interval == 0 and _matches_month_day(
            template, candidate
        )
    if template.frequency == "quarterly":
        return months_since_start % (3 * template.interval) == 0 and _matches_month_day(
            template, candidate
        )
    if template.frequency == "annual":
        year_index = candidate.year - template.starts_on.year
        target_month = template.month_of_year or template.starts_on.month
        return (
            year_index >= 0
            and year_index % template.interval == 0
            and candidate.month == target_month
            and _matches_month_day(template, candidate)
        )
    return False


def build_occurrences(
    template: EventTemplate,
    from_date: date,
    to_date: date,
    *,
    limit: int = 500,
) -> list[EventOccurrence]:
    """Return local-rule occurrences converted to timezone-aware datetimes."""
    start = max(from_date, template.starts_on)
    end = min(to_date, template.ends_on) if template.ends_on else to_date
    if end < start:
        return []

    timezone = ZoneInfo(template.timezone)
    occurrences: list[EventOccurrence] = []
    candidate = start
    while candidate <= end:
        if _matches(template, candidate):
            local_start = datetime.combine(
                candidate, template.local_start_time, tzinfo=timezone
            )
            local_end = local_start + timedelta(minutes=template.duration_minutes)
            occurrences.append(
                EventOccurrence(
                    local_date=candidate,
                    start_time=local_start,
                    end_time=local_end,
                    external_key=f"event-template:{template.id}:{candidate.isoformat()}",
                )
            )
            if len(occurrences) >= limit:
                break
        candidate += timedelta(days=1)
    return occurrences
