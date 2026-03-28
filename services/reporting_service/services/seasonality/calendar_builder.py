"""Build the 12-month operational seasonality calendar."""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field

from services.reporting_service.services.seasonality.priors import (
    ACTION_RULES,
    LAGOS_MONTHLY_RAINFALL_MM,
    LAGOS_PUBLIC_HOLIDAYS_2026,
    LAGOS_SCHOOL_CALENDAR,
    rainfall_category,
)
from services.reporting_service.services.seasonality.scorer import (
    classify_status,
    confidence_band,
    demand_level,
)


@dataclass
class CalendarEntry:
    """One month in the operational seasonality calendar."""

    month: int
    month_name: str
    demand_level: str
    expected_demand: float
    lower_bound: float
    upper_bound: float
    seasonal_index: float
    trend_factor: float
    status_label: str
    recommended_actions: list[str] = field(default_factory=list)
    key_factors: list[str] = field(default_factory=list)


def _key_factors_for_month(month: int, year: int) -> list[str]:
    """Summarise the external factors driving demand for a month."""
    factors = []

    # Rainfall
    rain = LAGOS_MONTHLY_RAINFALL_MM.get(month, 0)
    cat = rainfall_category(rain)
    if cat in ("heavy", "peak"):
        factors.append(f"Heavy rainfall ({rain:.0f}mm)")
    elif cat == "moderate":
        factors.append(f"Moderate rainfall ({rain:.0f}mm)")
    elif cat == "dry":
        factors.append("Dry season")

    # School
    school = LAGOS_SCHOOL_CALENDAR.get(month, {})
    if school.get("holiday"):
        factors.append("School holiday")
    if school.get("exam_period"):
        factors.append("Exam period")

    # Public holidays
    holidays = LAGOS_PUBLIC_HOLIDAYS_2026.get(month, [])
    if holidays:
        factors.append(f"Holidays: {', '.join(holidays)}")

    # Month-end salary cycle (always true but only notable in context)
    if month in (1, 9):
        factors.append("New-term enrollment surge")
    if month == 12:
        factors.append("Holiday travel season")

    return factors


def build_calendar(
    monthly_forecasts: list,
    months_of_real_data: int,
    actuals_by_month: dict[int, float] | None = None,
    forecast_year: int = 2026,
) -> list[CalendarEntry]:
    """Build a 12-month operational calendar from forecast results.

    Args:
        monthly_forecasts: List of MonthForecast dataclass instances.
        months_of_real_data: How many months of actuals exist.
        actuals_by_month: {month: actual_attendance} for past months.
        forecast_year: The year being forecast.

    Returns:
        List of 12 CalendarEntry objects.
    """
    if actuals_by_month is None:
        actuals_by_month = {}

    entries = []
    for fc in monthly_forecasts:
        m = fc.month
        lower, upper = confidence_band(fc.expected_demand, months_of_real_data)
        level = demand_level(fc.seasonal_index)
        actual = actuals_by_month.get(m)
        status = classify_status(
            actual, fc.expected_demand, lower, upper, fc.seasonal_index
        )
        actions = list(ACTION_RULES.get(level, []))
        factors = _key_factors_for_month(m, forecast_year)

        entries.append(
            CalendarEntry(
                month=m,
                month_name=calendar.month_name[m],
                demand_level=level,
                expected_demand=fc.expected_demand,
                lower_bound=lower,
                upper_bound=upper,
                seasonal_index=fc.seasonal_index,
                trend_factor=fc.trend_factor,
                status_label=status,
                recommended_actions=actions,
                key_factors=factors,
            )
        )

    return entries
