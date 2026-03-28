"""Tests for the seasonality report renderers."""

import pytest

from services.reporting_service.services.seasonality.calendar_builder import (
    build_calendar,
)
from services.reporting_service.services.seasonality.model import compute_forecast
from services.reporting_service.services.seasonality.report_renderer import (
    render_csv,
    render_html,
    render_markdown,
)


@pytest.fixture
def sample_calendar():
    """Build a calendar from default forecasts."""
    forecasts = compute_forecast(trend_rate=0.0)
    return build_calendar(forecasts, months_of_real_data=0, forecast_year=2026)


class TestRenderMarkdown:
    def test_contains_title(self, sample_calendar):
        md = render_markdown(
            sample_calendar,
            forecast_year=2026,
            months_of_real_data=0,
            prior_weight_pct=100,
            model_params={
                "baseline": 150,
                "trend_rate": 0.015,
                "launch_year": 2026,
                "launch_month": 1,
            },
        )
        assert "SwimBuddz Seasonality Model" in md
        assert "2026" in md

    def test_contains_data_quality_notice(self, sample_calendar):
        md = render_markdown(
            sample_calendar,
            forecast_year=2026,
            months_of_real_data=2,
            prior_weight_pct=80,
            model_params={
                "baseline": 150,
                "trend_rate": 0.015,
                "launch_year": 2026,
                "launch_month": 1,
            },
        )
        assert "Data quality notice" in md

    def test_no_notice_with_enough_data(self, sample_calendar):
        md = render_markdown(
            sample_calendar,
            forecast_year=2026,
            months_of_real_data=12,
            prior_weight_pct=25,
            model_params={
                "baseline": 150,
                "trend_rate": 0.015,
                "launch_year": 2026,
                "launch_month": 1,
            },
        )
        assert "Data quality notice" not in md

    def test_contains_all_months(self, sample_calendar):
        md = render_markdown(
            sample_calendar,
            forecast_year=2026,
            months_of_real_data=0,
            prior_weight_pct=100,
            model_params={
                "baseline": 150,
                "trend_rate": 0.015,
                "launch_year": 2026,
                "launch_month": 1,
            },
        )
        for month_name in ["January", "February", "July", "December"]:
            assert month_name in md

    def test_contains_decision_rules(self, sample_calendar):
        md = render_markdown(
            sample_calendar,
            forecast_year=2026,
            months_of_real_data=0,
            prior_weight_pct=100,
            model_params={
                "baseline": 150,
                "trend_rate": 0.015,
                "launch_year": 2026,
                "launch_month": 1,
            },
        )
        assert "Decision Rules" in md or "How to Read" in md


class TestRenderCsv:
    def test_has_header_row(self, sample_calendar):
        csv_content = render_csv(sample_calendar)
        lines = csv_content.strip().split("\n")
        assert len(lines) == 13  # header + 12 months
        assert "Month" in lines[0]
        assert "Demand_Level" in lines[0]

    def test_12_data_rows(self, sample_calendar):
        csv_content = render_csv(sample_calendar)
        lines = csv_content.strip().split("\n")
        data_rows = lines[1:]
        assert len(data_rows) == 12

    def test_parseable_csv(self, sample_calendar):
        import csv
        import io

        csv_content = render_csv(sample_calendar)
        reader = csv.DictReader(io.StringIO(csv_content))
        rows = list(reader)
        assert len(rows) == 12
        assert rows[0]["Month_Name"] == "January"
        assert float(rows[0]["Expected_Demand"]) > 0


class TestRenderHtml:
    def test_contains_chartjs(self, sample_calendar):
        html = render_html(sample_calendar, 2026, 0, 100)
        assert "chart.js" in html.lower() or "Chart" in html

    def test_contains_title(self, sample_calendar):
        html = render_html(sample_calendar, 2026, 0, 100)
        assert "SwimBuddz" in html
        assert "2026" in html

    def test_contains_table(self, sample_calendar):
        html = render_html(sample_calendar, 2026, 0, 100)
        assert "<table>" in html
        assert "January" in html

    def test_contains_warning_for_low_data(self, sample_calendar):
        html = render_html(sample_calendar, 2026, 2, 80)
        assert "fewer than 6 months" in html

    def test_no_warning_for_enough_data(self, sample_calendar):
        html = render_html(sample_calendar, 2026, 12, 25)
        assert "fewer than 6 months" not in html

    def test_valid_html_structure(self, sample_calendar):
        html = render_html(sample_calendar, 2026, 0, 100)
        assert html.startswith("<!DOCTYPE html>")
        assert "</html>" in html
