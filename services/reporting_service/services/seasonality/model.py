"""Multiplicative decomposition forecasting model.

Formula:
    expected(month) = baseline × seasonal_index(month) × trend(month) × campaign(month)

All functions are pure (no DB or I/O) so they're easy to test.
"""

from __future__ import annotations

from dataclasses import dataclass

from services.reporting_service.services.seasonality.priors import (
    DEFAULT_BASELINE_ATTENDANCE,
    DEFAULT_TREND_RATE,
    LAGOS_SEASONAL_INDICES,
)


@dataclass
class MonthForecast:
    """Forecast output for a single month."""

    month: int  # 1-12
    baseline: float
    seasonal_index: float
    trend_factor: float
    campaign_multiplier: float
    expected_demand: float  # final forecast value


def trend_factor(month_offset: int, monthly_rate: float) -> float:
    """Compound growth/decline from launch month.

    Args:
        month_offset: Months since launch (0 = launch month).
        monthly_rate: Growth rate per month (e.g. 0.015 = 1.5%).

    Returns:
        Multiplicative trend factor.
    """
    return (1 + monthly_rate) ** month_offset


def compute_forecast(
    baseline: float = DEFAULT_BASELINE_ATTENDANCE,
    seasonal_indices: dict[int, float] | None = None,
    trend_rate: float = DEFAULT_TREND_RATE,
    campaign_multipliers: dict[int, float] | None = None,
    launch_year: int = 2026,
    launch_month: int = 1,
    forecast_year: int = 2026,
) -> list[MonthForecast]:
    """Generate 12 monthly forecasts for *forecast_year*.

    Args:
        baseline: Average monthly demand (de-seasonalised).
        seasonal_indices: {month: multiplier} — defaults to Lagos priors.
        trend_rate: Monthly compound growth rate.
        campaign_multipliers: {month: multiplier} — defaults to 1.0 everywhere.
        launch_year: Year the platform launched (for trend calculation).
        launch_month: Month the platform launched.
        forecast_year: Year to forecast.

    Returns:
        List of 12 MonthForecast objects (Jan–Dec).
    """
    if seasonal_indices is None:
        seasonal_indices = dict(LAGOS_SEASONAL_INDICES)
    if campaign_multipliers is None:
        campaign_multipliers = {}

    results: list[MonthForecast] = []
    for m in range(1, 13):
        # Months elapsed since launch
        offset = (forecast_year - launch_year) * 12 + (m - launch_month)
        offset = max(offset, 0)

        s = seasonal_indices.get(m, 1.0)
        t = trend_factor(offset, trend_rate)
        c = campaign_multipliers.get(m, 1.0)
        expected = baseline * s * t * c

        results.append(
            MonthForecast(
                month=m,
                baseline=round(baseline, 1),
                seasonal_index=round(s, 3),
                trend_factor=round(t, 4),
                campaign_multiplier=round(c, 2),
                expected_demand=round(expected, 1),
            )
        )
    return results
