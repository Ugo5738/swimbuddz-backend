"""Quarter date range utilities."""

from datetime import datetime
from zoneinfo import ZoneInfo

LAGOS_TZ = ZoneInfo("Africa/Lagos")

# Quarter definitions: (start_month, start_day) to (end_month, end_day)
QUARTER_RANGES = {
    1: ((1, 1), (3, 31)),
    2: ((4, 1), (6, 30)),
    3: ((7, 1), (9, 30)),
    4: ((10, 1), (12, 31)),
}


def quarter_date_range(year: int, quarter: int) -> tuple[datetime, datetime]:
    """Return (start, end) datetimes for a given quarter in Africa/Lagos timezone.

    Both start and end are timezone-aware. Start is midnight on the first day,
    end is 23:59:59 on the last day.
    """
    if quarter not in QUARTER_RANGES:
        raise ValueError(f"Quarter must be 1-4, got {quarter}")

    (start_month, start_day), (end_month, end_day) = QUARTER_RANGES[quarter]

    start = datetime(year, start_month, start_day, 0, 0, 0, tzinfo=LAGOS_TZ)
    end = datetime(year, end_month, end_day, 23, 59, 59, tzinfo=LAGOS_TZ)
    return start, end


def current_quarter(dt: datetime | None = None) -> tuple[int, int]:
    """Return (year, quarter) for a given datetime or now."""
    if dt is None:
        dt = datetime.now(LAGOS_TZ)
    month = dt.month
    quarter = (month - 1) // 3 + 1
    return dt.year, quarter


def quarter_label(year: int, quarter: int) -> str:
    """Human-readable quarter label, e.g. 'Q1 2026'."""
    return f"Q{quarter} {year}"
