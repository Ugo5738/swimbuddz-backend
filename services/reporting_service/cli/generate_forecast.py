"""CLI to generate a SwimBuddz seasonality forecast.

Usage:
    cd swimbuddz-backend
    python -m services.reporting_service.cli.generate_forecast --year 2026
    python -m services.reporting_service.cli.generate_forecast --year 2026 --output-dir ./reports/seasonality
    python -m services.reporting_service.cli.generate_forecast --year 2026 --baseline 200 --trend 0.02

This generates:
    - SEASONALITY_MODEL_{year}.md   — Full method + calendar document
    - SEASONALITY_CALENDAR_{year}.csv — Spreadsheet-ready calendar
    - seasonality_report_{year}.html — Self-contained visual report

The script works both WITH and WITHOUT a database:
    - With DB: reads stored actuals and external factors for calibration
    - Without DB: uses Lagos domain priors only (great for first-time use)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure project root on sys.path for imports
PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT))

from services.reporting_service.services.seasonality.calendar_builder import (
    build_calendar,
)
from services.reporting_service.services.seasonality.calibrator import (
    calibrate_seasonal_indices,
    estimate_baseline,
    prior_weight,
)
from services.reporting_service.services.seasonality.model import compute_forecast
from services.reporting_service.services.seasonality.priors import (
    DEFAULT_BASELINE_ATTENDANCE,
    DEFAULT_TREND_RATE,
)
from services.reporting_service.services.seasonality.report_renderer import (
    render_csv,
    render_html,
    render_markdown,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate SwimBuddz seasonality forecast"
    )
    parser.add_argument(
        "--year",
        type=int,
        required=True,
        help="Year to forecast (e.g. 2026)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="./reports/seasonality",
        help="Directory for output files (default: ./reports/seasonality)",
    )
    parser.add_argument(
        "--baseline",
        type=float,
        default=None,
        help=f"Override baseline attendance (default: {DEFAULT_BASELINE_ATTENDANCE})",
    )
    parser.add_argument(
        "--trend",
        type=float,
        default=None,
        help=f"Override monthly growth rate (default: {DEFAULT_TREND_RATE})",
    )
    parser.add_argument(
        "--actuals",
        type=str,
        default=None,
        help="Path to CSV with actual data (columns: month,total_attendance)",
    )
    args = parser.parse_args()

    year = args.year
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n🏊 SwimBuddz Seasonality Forecast — {year}")
    print("=" * 50)

    # Load actuals from CSV if provided
    actuals_list: list[dict] = []
    actuals_by_month: dict[int, float] = {}
    if args.actuals:
        import csv

        with open(args.actuals) as f:
            reader = csv.DictReader(f)
            for row in reader:
                m = int(row["month"])
                att = int(row["total_attendance"])
                actuals_list.append({"month": m, "total_attendance": att})
                actuals_by_month[m] = att
        print(f"📊 Loaded {len(actuals_list)} months of actuals from {args.actuals}")

    months_of_data = len(actuals_list)
    pw = prior_weight(months_of_data)

    # Calibrate
    baseline = args.baseline or estimate_baseline(actuals_list)
    trend_rate = args.trend or DEFAULT_TREND_RATE
    seasonal_indices = calibrate_seasonal_indices(actuals_list, baseline)

    print("\n📐 Model parameters:")
    print(f"   Baseline:        {baseline:.0f} attendance/month")
    print(f"   Trend:           {trend_rate*100:.1f}% monthly growth")
    print(f"   Data months:     {months_of_data}")
    print(f"   Prior weight:    {pw*100:.0f}%")

    # Run model
    forecasts = compute_forecast(
        baseline=baseline,
        seasonal_indices=seasonal_indices,
        trend_rate=trend_rate,
        forecast_year=year,
    )

    # Build calendar
    cal = build_calendar(
        monthly_forecasts=forecasts,
        months_of_real_data=months_of_data,
        actuals_by_month=actuals_by_month,
        forecast_year=year,
    )

    # Print summary
    print("\n📅 Forecast Summary:")
    print(f"   {'Month':<12} {'Level':<10} {'Expected':>8}  {'Range':>16}")
    print(f"   {'─'*12} {'─'*10} {'─'*8}  {'─'*16}")
    for e in cal:
        print(
            f"   {e.month_name:<12} {e.demand_level:<10} {e.expected_demand:>8.0f}  "
            f"{e.lower_bound:>7.0f} – {e.upper_bound:<7.0f}"
        )

    total = sum(e.expected_demand for e in cal)
    print(f"\n   Total expected attendance: {total:.0f}")

    model_params = {
        "baseline": round(baseline, 1),
        "trend_rate": trend_rate,
        "launch_year": 2026,
        "launch_month": 1,
        "seasonal_indices": seasonal_indices,
    }

    # Render outputs
    md_path = output_dir / f"SEASONALITY_MODEL_{year}.md"
    csv_path = output_dir / f"SEASONALITY_CALENDAR_{year}.csv"
    html_path = output_dir / f"seasonality_report_{year}.html"

    md_content = render_markdown(
        cal,
        forecast_year=year,
        months_of_real_data=months_of_data,
        prior_weight_pct=pw * 100,
        model_params=model_params,
    )
    md_path.write_text(md_content)

    csv_content = render_csv(cal)
    csv_path.write_text(csv_content)

    html_content = render_html(
        cal,
        forecast_year=year,
        months_of_real_data=months_of_data,
        prior_weight_pct=pw * 100,
    )
    html_path.write_text(html_content)

    print(f"\n✅ Output files written to {output_dir}/")
    print(f"   📄 {md_path.name}")
    print(f"   📊 {csv_path.name}")
    print(f"   🌐 {html_path.name}")
    print(f"\n   Open the HTML report: open {html_path}")


if __name__ == "__main__":
    main()
