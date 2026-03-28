"""Render seasonality forecast outputs in Markdown, CSV, and HTML."""

from __future__ import annotations

import csv
import io
import json

from services.reporting_service.services.seasonality.calendar_builder import (
    CalendarEntry,
)

# ── Markdown renderer ──


def render_markdown(
    calendar: list[CalendarEntry],
    forecast_year: int,
    months_of_real_data: int,
    prior_weight_pct: float,
    model_params: dict,
) -> str:
    """Render the full SEASONALITY_MODEL.md document."""
    lines: list[str] = []
    _w = lines.append

    _w(f"# SwimBuddz Seasonality Model — {forecast_year}")
    _w("")
    _w("## Executive Summary")
    _w("")
    _w(
        f"This forecast covers **{forecast_year}** and is based on "
        f"**{months_of_real_data} month(s)** of real platform data "
        f"blended with Lagos domain priors (prior weight: {prior_weight_pct:.0f}%)."
    )
    _w("")

    if months_of_real_data < 6:
        _w(
            "> **Data quality notice:** With fewer than 6 months of data, this forecast "
            "relies heavily on domain assumptions about Lagos seasonality. Confidence "
            "bands are wide to reflect this uncertainty. Treat as directional guidance, "
            "not precise prediction."
        )
        _w("")

    # Seasonal index table
    _w("## Seasonal Indices")
    _w("")
    _w("| Month | Index | Demand Level | Expected | Confidence Band |")
    _w("|-------|-------|-------------|----------|-----------------|")
    for e in calendar:
        _w(
            f"| {e.month_name} | {e.seasonal_index:.2f} | {e.demand_level.title()} "
            f"| {e.expected_demand:.0f} | {e.lower_bound:.0f} – {e.upper_bound:.0f} |"
        )
    _w("")

    # 12-month operational calendar
    _w("## 12-Month Operational Calendar")
    _w("")
    for e in calendar:
        icon = {"low": "🔵", "moderate": "🟡", "high": "🟠", "peak": "🔴"}.get(
            e.demand_level, "⚪"
        )
        _w(f"### {icon} {e.month_name} — {e.demand_level.upper()}")
        _w("")
        _w(
            f"**Expected demand:** {e.expected_demand:.0f} "
            f"(range: {e.lower_bound:.0f}–{e.upper_bound:.0f})"
        )
        _w(f"**Status:** {e.status_label.replace('_', ' ').title()}")
        _w("")
        if e.key_factors:
            _w("**Key factors:**")
            for f in e.key_factors:
                _w(f"- {f}")
            _w("")
        _w("**Recommended actions:**")
        for a in e.recommended_actions:
            _w(f"- {a}")
        _w("")

    # How to read this model
    _w("## How to Read This Model")
    _w("")
    _w("### The Formula")
    _w("")
    _w("```")
    _w("Expected Demand = Baseline × Seasonal Index × Trend × Campaign Multiplier")
    _w("```")
    _w("")
    _w("- **Baseline** = average monthly demand with seasonality removed")
    _w("- **Seasonal Index** = month-specific multiplier (1.0 = average month)")
    _w("- **Trend** = compound growth since launch")
    _w("- **Campaign Multiplier** = manual boost for known events/promos")
    _w("")
    _w("### Status Labels")
    _w("")
    _w("| Status | Meaning | Action |")
    _w("|--------|---------|--------|")
    _w(
        "| Expected Seasonal Dip | Demand is *supposed* to be low this month | "
        "Don't panic. Run retention, not acquisition. |"
    )
    _w("| On Track | Actual demand matches forecast | " "Continue as planned. |")
    _w(
        "| Outperforming | Actual demand exceeds upper bound | "
        "Investigate what worked — double down. |"
    )
    _w(
        "| Underperforming | Actual demand below lower bound | "
        "Investigate. Is it operational? Market? Competition? |"
    )
    _w("")
    _w("### Decision Rules")
    _w("")
    _w("| Situation | Action |")
    _w("|-----------|--------|")
    _w(
        "| Rainy season dip (Jun–Aug) | Normal. Focus on retention and indoor options. |"
    )
    _w("| Jan–Feb below forecast | Investigate — these should be peak months. |")
    _w("| Two consecutive months underperforming | Operational review needed. |")
    _w("| School holiday + low demand | Expected. Use for planning and content. |")
    _w("| Month-end spike | Normal salary-cycle effect. Schedule promos accordingly. |")
    _w("")

    # Model parameters
    _w("## Model Parameters")
    _w("")
    _w(f"- **Baseline:** {model_params.get('baseline', 'N/A')}")
    _w(f"- **Trend rate:** {model_params.get('trend_rate', 'N/A')} per month")
    _w(
        f"- **Launch date:** {model_params.get('launch_year', 'N/A')}-"
        f"{model_params.get('launch_month', 'N/A'):02d}"
    )
    _w(f"- **Months of real data:** {months_of_real_data}")
    _w(f"- **Prior weight:** {prior_weight_pct:.0f}%")
    _w("")

    _w("---")
    _w(f"*Generated for {forecast_year}. Rerun monthly to incorporate new data.*")

    return "\n".join(lines)


# ── CSV renderer ──


def render_csv(calendar: list[CalendarEntry]) -> str:
    """Render the seasonality calendar as CSV."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "Month",
            "Month_Name",
            "Seasonal_Index",
            "Demand_Level",
            "Expected_Demand",
            "Lower_Bound",
            "Upper_Bound",
            "Trend_Factor",
            "Status",
            "Key_Factors",
            "Recommended_Actions",
        ]
    )
    for e in calendar:
        writer.writerow(
            [
                e.month,
                e.month_name,
                f"{e.seasonal_index:.3f}",
                e.demand_level,
                f"{e.expected_demand:.0f}",
                f"{e.lower_bound:.0f}",
                f"{e.upper_bound:.0f}",
                f"{e.trend_factor:.4f}",
                e.status_label,
                "; ".join(e.key_factors),
                "; ".join(e.recommended_actions),
            ]
        )
    return buf.getvalue()


# ── HTML renderer ──


def render_html(
    calendar: list[CalendarEntry],
    forecast_year: int,
    months_of_real_data: int,
    prior_weight_pct: float,
) -> str:
    """Render a self-contained HTML report with Chart.js visualisations."""
    months_json = json.dumps([e.month_name[:3] for e in calendar])
    expected_json = json.dumps([round(e.expected_demand, 1) for e in calendar])
    lower_json = json.dumps([round(e.lower_bound, 1) for e in calendar])
    upper_json = json.dumps([round(e.upper_bound, 1) for e in calendar])
    indices_json = json.dumps([e.seasonal_index for e in calendar])
    _levels_json = json.dumps([e.demand_level for e in calendar])  # noqa: F841

    # Colour map for demand levels
    bar_colors_json = json.dumps(
        [
            {
                "low": "#3b82f6",
                "moderate": "#eab308",
                "high": "#f97316",
                "peak": "#ef4444",
            }.get(e.demand_level, "#9ca3af")
            for e in calendar
        ]
    )

    # Calendar table rows
    cal_rows = ""
    for e in calendar:
        level_badge = {
            "low": '<span style="background:#dbeafe;color:#1d4ed8;padding:2px 8px;border-radius:4px;font-size:12px">LOW</span>',
            "moderate": '<span style="background:#fef9c3;color:#a16207;padding:2px 8px;border-radius:4px;font-size:12px">MODERATE</span>',
            "high": '<span style="background:#fed7aa;color:#c2410c;padding:2px 8px;border-radius:4px;font-size:12px">HIGH</span>',
            "peak": '<span style="background:#fecaca;color:#dc2626;padding:2px 8px;border-radius:4px;font-size:12px">PEAK</span>',
        }.get(e.demand_level, "")
        status_badge = {
            "expected_seasonal_dip": '<span style="color:#3b82f6">Expected Dip</span>',
            "on_track": '<span style="color:#16a34a">On Track</span>',
            "outperforming": '<span style="color:#7c3aed">Outperforming</span>',
            "underperforming": '<span style="color:#dc2626">Underperforming</span>',
        }.get(e.status_label, e.status_label)
        factors = "<br>".join(e.key_factors) if e.key_factors else "—"
        actions = "<br>".join(f"• {a}" for a in e.recommended_actions[:3])
        cal_rows += f"""<tr>
            <td><strong>{e.month_name}</strong></td>
            <td>{level_badge}</td>
            <td>{e.expected_demand:.0f}</td>
            <td>{e.lower_bound:.0f} – {e.upper_bound:.0f}</td>
            <td>{status_badge}</td>
            <td style="font-size:13px">{factors}</td>
            <td style="font-size:13px">{actions}</td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SwimBuddz Seasonality Forecast — {forecast_year}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
           color: #1e293b; background: #f8fafc; padding: 24px; max-width: 1200px; margin: 0 auto; }}
    h1 {{ font-size: 28px; margin-bottom: 8px; color: #0f172a; }}
    h2 {{ font-size: 20px; margin: 32px 0 16px; color: #334155; }}
    .subtitle {{ color: #64748b; font-size: 14px; margin-bottom: 24px; }}
    .notice {{ background: #fef3c7; border-left: 4px solid #f59e0b; padding: 12px 16px;
               border-radius: 4px; margin-bottom: 24px; font-size: 14px; color: #92400e; }}
    .charts {{ display: grid; grid-template-columns: 1fr 1fr; gap: 24px; margin-bottom: 32px; }}
    .chart-box {{ background: white; border-radius: 8px; padding: 20px;
                  box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
    @media (max-width: 768px) {{ .charts {{ grid-template-columns: 1fr; }} }}
    table {{ width: 100%; border-collapse: collapse; background: white;
             border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
    th {{ background: #f1f5f9; text-align: left; padding: 10px 12px; font-size: 13px;
         color: #475569; border-bottom: 2px solid #e2e8f0; }}
    td {{ padding: 10px 12px; border-bottom: 1px solid #f1f5f9; vertical-align: top; }}
    tr:hover {{ background: #f8fafc; }}
    .footer {{ text-align: center; color: #94a3b8; font-size: 12px; margin-top: 32px; padding: 16px; }}
    .kpi-row {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
                gap: 16px; margin-bottom: 24px; }}
    .kpi {{ background: white; border-radius: 8px; padding: 16px; text-align: center;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
    .kpi-value {{ font-size: 28px; font-weight: 700; color: #0f172a; }}
    .kpi-label {{ font-size: 12px; color: #64748b; margin-top: 4px; }}
</style>
</head>
<body>
<h1>🏊 SwimBuddz Seasonality Forecast — {forecast_year}</h1>
<p class="subtitle">Generated with {months_of_real_data} month(s) of data · Prior weight: {prior_weight_pct:.0f}%</p>

{"<div class='notice'>⚠️ With fewer than 6 months of data, this forecast relies heavily on domain assumptions. Confidence bands are wide. Treat as directional guidance.</div>" if months_of_real_data < 6 else ""}

<div class="kpi-row">
    <div class="kpi">
        <div class="kpi-value">{sum(e.expected_demand for e in calendar):.0f}</div>
        <div class="kpi-label">Total Expected Attendance</div>
    </div>
    <div class="kpi">
        <div class="kpi-value">{max(calendar, key=lambda e: e.expected_demand).month_name[:3]}</div>
        <div class="kpi-label">Peak Month</div>
    </div>
    <div class="kpi">
        <div class="kpi-value">{min(calendar, key=lambda e: e.expected_demand).month_name[:3]}</div>
        <div class="kpi-label">Lowest Month</div>
    </div>
    <div class="kpi">
        <div class="kpi-value">{sum(1 for e in calendar if e.demand_level in ('low',))}</div>
        <div class="kpi-label">Low-Demand Months</div>
    </div>
</div>

<div class="charts">
    <div class="chart-box">
        <canvas id="forecastChart"></canvas>
    </div>
    <div class="chart-box">
        <canvas id="indexChart"></canvas>
    </div>
</div>

<h2>12-Month Operational Calendar</h2>
<table>
<thead>
<tr>
    <th>Month</th><th>Level</th><th>Expected</th><th>Confidence</th>
    <th>Status</th><th>Key Factors</th><th>Actions</th>
</tr>
</thead>
<tbody>
{cal_rows}
</tbody>
</table>

<div class="footer">
    SwimBuddz Seasonality Model · {forecast_year} · Rerun monthly to incorporate new data
</div>

<script>
const months = {months_json};
const expected = {expected_json};
const lower = {lower_json};
const upper = {upper_json};
const indices = {indices_json};
const barColors = {bar_colors_json};

// Forecast line chart with confidence band
new Chart(document.getElementById('forecastChart'), {{
    type: 'line',
    data: {{
        labels: months,
        datasets: [
            {{
                label: 'Upper Bound',
                data: upper,
                borderColor: 'transparent',
                backgroundColor: 'rgba(59, 130, 246, 0.1)',
                fill: '+1',
                pointRadius: 0,
            }},
            {{
                label: 'Expected Demand',
                data: expected,
                borderColor: '#3b82f6',
                backgroundColor: 'rgba(59, 130, 246, 0.1)',
                borderWidth: 3,
                fill: false,
                pointBackgroundColor: '#3b82f6',
                tension: 0.3,
            }},
            {{
                label: 'Lower Bound',
                data: lower,
                borderColor: 'transparent',
                backgroundColor: 'transparent',
                fill: false,
                pointRadius: 0,
            }},
        ]
    }},
    options: {{
        responsive: true,
        plugins: {{
            title: {{ display: true, text: 'Monthly Demand Forecast with Confidence Band' }},
            legend: {{ display: false }},
        }},
        scales: {{
            y: {{ beginAtZero: true, title: {{ display: true, text: 'Attendance' }} }}
        }}
    }}
}});

// Seasonal index bar chart
new Chart(document.getElementById('indexChart'), {{
    type: 'bar',
    data: {{
        labels: months,
        datasets: [{{
            label: 'Seasonal Index',
            data: indices,
            backgroundColor: barColors,
            borderRadius: 4,
        }}]
    }},
    options: {{
        responsive: true,
        plugins: {{
            title: {{ display: true, text: 'Seasonal Demand Indices (1.0 = average)' }},
            legend: {{ display: false }},
            annotation: {{
                annotations: {{
                    baseline: {{
                        type: 'line', yMin: 1.0, yMax: 1.0,
                        borderColor: '#94a3b8', borderDash: [5, 5], borderWidth: 1,
                    }}
                }}
            }}
        }},
        scales: {{
            y: {{ min: 0.5, max: 1.3, title: {{ display: true, text: 'Index' }} }}
        }}
    }}
}});
</script>
</body>
</html>"""
