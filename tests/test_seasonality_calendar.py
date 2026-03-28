"""Tests for the seasonality calendar builder."""

import pytest

from services.reporting_service.services.seasonality.calendar_builder import (
    build_calendar,
)
from services.reporting_service.services.seasonality.model import compute_forecast


@pytest.fixture
def default_forecasts():
    """Generate forecasts with default parameters."""
    return compute_forecast(trend_rate=0.0)


class TestBuildCalendar:
    def test_returns_12_entries(self, default_forecasts):
        cal = build_calendar(default_forecasts, months_of_real_data=0)
        assert len(cal) == 12

    def test_all_months_have_names(self, default_forecasts):
        cal = build_calendar(default_forecasts, months_of_real_data=0)
        names = [e.month_name for e in cal]
        assert "January" in names
        assert "December" in names

    def test_demand_levels_assigned(self, default_forecasts):
        cal = build_calendar(default_forecasts, months_of_real_data=0)
        levels = {e.demand_level for e in cal}
        # With Lagos priors, we should have at least low and peak
        assert "low" in levels or "moderate" in levels
        assert "peak" in levels or "high" in levels

    def test_recommended_actions_populated(self, default_forecasts):
        cal = build_calendar(default_forecasts, months_of_real_data=0)
        for e in cal:
            assert len(e.recommended_actions) > 0

    def test_july_is_low_demand(self, default_forecasts):
        cal = build_calendar(default_forecasts, months_of_real_data=0)
        july = next(e for e in cal if e.month == 7)
        assert july.demand_level == "low"

    def test_january_is_peak(self, default_forecasts):
        cal = build_calendar(default_forecasts, months_of_real_data=0)
        jan = next(e for e in cal if e.month == 1)
        assert jan.demand_level == "peak"

    def test_confidence_bounds_present(self, default_forecasts):
        cal = build_calendar(default_forecasts, months_of_real_data=0)
        for e in cal:
            assert e.lower_bound < e.expected_demand < e.upper_bound

    def test_actuals_affect_status(self, default_forecasts):
        """When actual is way above upper bound, should be outperforming."""
        cal = build_calendar(
            default_forecasts,
            months_of_real_data=3,
            actuals_by_month={1: 9999},  # Absurdly high
        )
        jan = next(e for e in cal if e.month == 1)
        assert jan.status_label == "outperforming"

    def test_low_actual_is_underperforming(self, default_forecasts):
        cal = build_calendar(
            default_forecasts,
            months_of_real_data=3,
            actuals_by_month={1: 1},  # Absurdly low
        )
        jan = next(e for e in cal if e.month == 1)
        assert jan.status_label == "underperforming"

    def test_key_factors_populated(self, default_forecasts):
        cal = build_calendar(
            default_forecasts, months_of_real_data=0, forecast_year=2026
        )
        # April should mention Easter
        apr = next(e for e in cal if e.month == 4)
        factors_text = " ".join(apr.key_factors)
        assert "Easter" in factors_text or "rain" in factors_text.lower()
