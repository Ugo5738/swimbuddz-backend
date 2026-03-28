"""Unit tests for the seasonality model engine.

Tests cover the pure-function core: model, calibrator, and scorer.
No database or service dependencies required.
"""

import pytest

from services.reporting_service.services.seasonality.calibrator import (
    blend_prior_and_data,
    calibrate_seasonal_indices,
    estimate_baseline,
    prior_weight,
)
from services.reporting_service.services.seasonality.model import (
    compute_forecast,
    trend_factor,
)
from services.reporting_service.services.seasonality.priors import (
    DEFAULT_BASELINE_ATTENDANCE,
    LAGOS_SEASONAL_INDICES,
)
from services.reporting_service.services.seasonality.scorer import (
    classify_status,
    confidence_band,
    demand_level,
    uncertainty_factor,
)

# ── Model tests ──


class TestTrendFactor:
    def test_zero_offset_returns_one(self):
        assert trend_factor(0, 0.015) == 1.0

    def test_positive_growth(self):
        result = trend_factor(12, 0.015)
        expected = (1.015) ** 12
        assert abs(result - expected) < 0.001

    def test_no_growth(self):
        assert trend_factor(24, 0.0) == 1.0

    def test_negative_growth(self):
        result = trend_factor(6, -0.01)
        assert result < 1.0


class TestComputeForecast:
    def test_returns_12_months(self):
        result = compute_forecast()
        assert len(result) == 12

    def test_all_months_present(self):
        result = compute_forecast()
        months = [f.month for f in result]
        assert months == list(range(1, 13))

    def test_expected_demand_is_positive(self):
        result = compute_forecast()
        for f in result:
            assert f.expected_demand > 0

    def test_custom_baseline(self):
        result = compute_forecast(baseline=200)
        for f in result:
            assert f.baseline == 200

    def test_flat_seasonality(self):
        """With all indices = 1.0 and no trend, all months equal baseline."""
        flat = {m: 1.0 for m in range(1, 13)}
        result = compute_forecast(
            baseline=100,
            seasonal_indices=flat,
            trend_rate=0.0,
            forecast_year=2026,
            launch_year=2026,
            launch_month=1,
        )
        for f in result:
            assert f.expected_demand == 100.0

    def test_july_is_lowest_with_default_priors(self):
        """July has the lowest seasonal index in Lagos priors."""
        result = compute_forecast(trend_rate=0.0)
        july = next(f for f in result if f.month == 7)
        for f in result:
            if f.month != 7:
                assert (
                    f.expected_demand >= july.expected_demand
                    or abs(f.expected_demand - july.expected_demand) < 1
                )

    def test_campaign_multiplier_applied(self):
        result_no_campaign = compute_forecast(baseline=100, trend_rate=0.0)
        result_with_campaign = compute_forecast(
            baseline=100,
            trend_rate=0.0,
            campaign_multipliers={1: 1.5},
        )
        jan_no = next(f for f in result_no_campaign if f.month == 1)
        jan_with = next(f for f in result_with_campaign if f.month == 1)
        assert jan_with.expected_demand == pytest.approx(
            jan_no.expected_demand * 1.5, rel=0.01
        )


# ── Calibrator tests ──


class TestPriorWeight:
    def test_zero_data_full_prior(self):
        assert prior_weight(0) == 1.0

    def test_weight_decreases_with_data(self):
        assert prior_weight(4) < prior_weight(0)
        assert prior_weight(12) < prior_weight(4)

    def test_minimum_weight(self):
        assert prior_weight(1000) == 0.05


class TestBlendPriorAndData:
    def test_no_observed_returns_prior(self):
        assert blend_prior_and_data(1.15, None, 0) == 1.15

    def test_zero_data_returns_prior(self):
        result = blend_prior_and_data(1.15, 0.9, 0)
        assert result == 1.15  # prior_weight = 1.0

    def test_lots_of_data_approaches_observed(self):
        result = blend_prior_and_data(1.15, 0.9, 100)
        assert abs(result - 0.9) < 0.02

    def test_medium_data_blends(self):
        result = blend_prior_and_data(1.0, 1.5, 4)
        # prior_weight = 0.5 at 4 months
        assert 1.0 < result < 1.5


class TestEstimateBaseline:
    def test_no_actuals_returns_default(self):
        assert estimate_baseline() == float(DEFAULT_BASELINE_ATTENDANCE)

    def test_empty_list_returns_default(self):
        assert estimate_baseline([]) == float(DEFAULT_BASELINE_ATTENDANCE)

    def test_single_actual(self):
        # Jan index = 1.15, so 115 attendance / 1.15 = 100 baseline
        result = estimate_baseline([{"month": 1, "total_attendance": 115}])
        assert abs(result - 100.0) < 1.0

    def test_multiple_actuals(self):
        actuals = [
            {"month": 1, "total_attendance": 115},
            {"month": 7, "total_attendance": 70},
        ]
        result = estimate_baseline(actuals)
        # Both should deseasonalise to ~100
        assert abs(result - 100.0) < 1.0


class TestCalibrateSeasonalIndices:
    def test_no_actuals_returns_priors(self):
        result = calibrate_seasonal_indices()
        assert result == LAGOS_SEASONAL_INDICES

    def test_returns_12_months(self):
        result = calibrate_seasonal_indices([{"month": 1, "total_attendance": 150}])
        assert len(result) == 12

    def test_indices_average_near_one(self):
        result = calibrate_seasonal_indices(
            [
                {"month": 1, "total_attendance": 150},
                {"month": 2, "total_attendance": 140},
                {"month": 3, "total_attendance": 130},
            ]
        )
        avg = sum(result.values()) / 12
        assert abs(avg - 1.0) < 0.01


# ── Scorer tests ──


class TestUncertaintyFactor:
    def test_high_uncertainty_with_no_data(self):
        u = uncertainty_factor(0)
        assert u == pytest.approx(0.45, abs=0.01)

    def test_decreases_with_data(self):
        assert uncertainty_factor(12) < uncertainty_factor(0)

    def test_floor(self):
        u = uncertainty_factor(1000)
        assert u == pytest.approx(0.10, abs=0.01)


class TestConfidenceBand:
    def test_symmetric_around_expected(self):
        lower, upper = confidence_band(100, 12)
        assert lower < 100 < upper

    def test_wider_with_less_data(self):
        _, upper_0 = confidence_band(100, 0)
        _, upper_12 = confidence_band(100, 12)
        assert upper_0 > upper_12

    def test_lower_bound_not_negative(self):
        lower, _ = confidence_band(10, 0)
        assert lower >= 0


class TestDemandLevel:
    def test_low(self):
        assert demand_level(0.70) == "low"

    def test_moderate(self):
        assert demand_level(0.85) == "moderate"

    def test_high(self):
        assert demand_level(1.00) == "high"

    def test_peak(self):
        assert demand_level(1.15) == "peak"


class TestClassifyStatus:
    def test_future_low_month(self):
        assert classify_status(None, 100, 80, 120, 0.75) == "expected_seasonal_dip"

    def test_future_normal_month(self):
        assert classify_status(None, 100, 80, 120, 1.0) == "on_track"

    def test_on_track(self):
        assert classify_status(100, 100, 80, 120, 1.0) == "on_track"

    def test_outperforming(self):
        assert classify_status(150, 100, 80, 120, 1.0) == "outperforming"

    def test_underperforming(self):
        assert classify_status(50, 100, 80, 120, 1.0) == "underperforming"

    def test_edge_on_upper_bound(self):
        assert classify_status(120, 100, 80, 120, 1.0) == "on_track"
