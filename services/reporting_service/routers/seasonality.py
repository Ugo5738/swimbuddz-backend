"""Admin-facing seasonality forecast endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, PlainTextResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from libs.db.session import get_async_db
from services.reporting_service.models.enums import DataSource, ForecastStatus
from services.reporting_service.models.seasonality import (
    ExternalFactor,
    MonthlyActual,
    SeasonalityForecast,
)
from services.reporting_service.schemas.seasonality import (
    ForecastDetailResponse,
    ForecastSummaryResponse,
    GenerateForecastRequest,
    IngestActualRequest,
    MonthCalendarEntry,
    MonthlyActualResponse,
    SeedExternalFactorsRequest,
)
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
    DEFAULT_TREND_RATE,
    LAGOS_MONTHLY_RAINFALL_MM,
    LAGOS_PUBLIC_HOLIDAYS_2026,
    LAGOS_SCHOOL_CALENDAR,
    rainfall_category,
)
from services.reporting_service.services.seasonality.report_renderer import (
    render_csv,
    render_html,
    render_markdown,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/admin/reports/seasonality", tags=["admin-seasonality"])


# ── Generate forecast ──


@router.post("/generate", response_model=ForecastSummaryResponse)
async def generate_forecast(
    body: GenerateForecastRequest,
    admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Generate (or regenerate) a seasonality forecast for a given year.

    Automatically ingests actuals from live services before forecasting.
    """
    year = body.forecast_year

    # Auto-ingest actuals from live services
    from services.reporting_service.services.seasonality.ingest import (
        ingest_all_available_months,
    )

    try:
        ingested = await ingest_all_available_months(year, db)
        logger.info(f"Auto-ingested {len(ingested)} months of actuals for {year}")
    except Exception as e:
        logger.warning(f"Auto-ingest failed (continuing with existing data): {e}")

    # Check for existing forecast
    if not body.force_regenerate:
        existing = await db.execute(
            select(SeasonalityForecast)
            .where(
                SeasonalityForecast.forecast_year == year,
                SeasonalityForecast.status == ForecastStatus.COMPLETED.value,
            )
            .order_by(SeasonalityForecast.generated_at.desc())
            .limit(1)
        )
        existing_fc = existing.scalar_one_or_none()
        if existing_fc:
            raise HTTPException(
                status_code=409,
                detail=f"Forecast for {year} already exists. Use force_regenerate=true to overwrite.",
            )

    # Fetch actuals
    result = await db.execute(select(MonthlyActual).where(MonthlyActual.year == year))
    actuals_rows = result.scalars().all()
    actuals_list = [
        {"month": a.month, "total_attendance": a.total_attendance} for a in actuals_rows
    ]
    actuals_by_month = {a.month: a.total_attendance for a in actuals_rows}

    months_of_data = len(actuals_list)
    pw = prior_weight(months_of_data)

    # Calibrate
    baseline = body.baseline_override or estimate_baseline(actuals_list)
    trend_rate = body.trend_rate_override or DEFAULT_TREND_RATE
    seasonal_indices = calibrate_seasonal_indices(actuals_list, baseline)

    # Fetch campaign multipliers from external factors
    ef_result = await db.execute(
        select(ExternalFactor).where(ExternalFactor.year == year)
    )
    ef_rows = ef_result.scalars().all()
    campaign_multipliers = {
        ef.month: ef.campaign_effect_multiplier
        for ef in ef_rows
        if ef.campaign_effect_multiplier != 1.0
    }

    # Run model
    forecasts = compute_forecast(
        baseline=baseline,
        seasonal_indices=seasonal_indices,
        trend_rate=trend_rate,
        campaign_multipliers=campaign_multipliers,
        forecast_year=year,
    )

    # Build calendar
    cal = build_calendar(
        monthly_forecasts=forecasts,
        months_of_real_data=months_of_data,
        actuals_by_month=actuals_by_month,
        forecast_year=year,
    )

    # Serialise monthly forecasts for JSONB storage
    monthly_json = [
        {
            "month": e.month,
            "month_name": e.month_name,
            "seasonal_index": e.seasonal_index,
            "trend_factor": e.trend_factor,
            "campaign_multiplier": forecasts[i].campaign_multiplier,
            "demand_level": e.demand_level,
            "expected_demand": e.expected_demand,
            "lower_bound": e.lower_bound,
            "upper_bound": e.upper_bound,
            "status_label": e.status_label,
            "recommended_actions": e.recommended_actions,
            "key_factors": e.key_factors,
        }
        for i, e in enumerate(cal)
    ]

    model_params = {
        "baseline": round(baseline, 1),
        "trend_rate": trend_rate,
        "launch_year": 2026,
        "launch_month": 1,
        "seasonal_indices": seasonal_indices,
        "campaign_multipliers": campaign_multipliers or {},
    }

    # Store forecast
    fc = SeasonalityForecast(
        forecast_year=year,
        generated_at=utc_now(),
        status=ForecastStatus.COMPLETED,
        model_params=model_params,
        monthly_forecasts=monthly_json,
        months_of_real_data=months_of_data,
        prior_weight=round(pw, 3),
    )
    db.add(fc)
    await db.commit()
    await db.refresh(fc)

    logger.info(
        f"Generated seasonality forecast for {year} (data months: {months_of_data})"
    )

    return ForecastSummaryResponse(
        id=fc.id,
        forecast_year=fc.forecast_year,
        generated_at=fc.generated_at,
        status=fc.status.value if hasattr(fc.status, "value") else fc.status,
        months_of_real_data=fc.months_of_real_data,
        prior_weight=fc.prior_weight,
    )


# ── Get forecast ──


async def _get_latest_forecast(year: int, db: AsyncSession) -> SeasonalityForecast:
    """Fetch the most recent completed forecast for a year."""
    result = await db.execute(
        select(SeasonalityForecast)
        .where(
            SeasonalityForecast.forecast_year == year,
            SeasonalityForecast.status == ForecastStatus.COMPLETED.value,
        )
        .order_by(SeasonalityForecast.generated_at.desc())
        .limit(1)
    )
    fc = result.scalar_one_or_none()
    if not fc:
        raise HTTPException(
            status_code=404,
            detail=f"No forecast found for {year}. Generate one first.",
        )
    return fc


@router.get("/forecast/{forecast_year}", response_model=ForecastDetailResponse)
async def get_forecast(
    forecast_year: int,
    admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Get the latest completed forecast for a year."""
    fc = await _get_latest_forecast(forecast_year, db)
    return ForecastDetailResponse(
        id=fc.id,
        forecast_year=fc.forecast_year,
        generated_at=fc.generated_at,
        status=fc.status.value if hasattr(fc.status, "value") else fc.status,
        months_of_real_data=fc.months_of_real_data,
        prior_weight=fc.prior_weight,
        monthly_forecasts=fc.monthly_forecasts,
        model_params=fc.model_params,
    )


@router.get(
    "/forecast/{forecast_year}/calendar", response_model=list[MonthCalendarEntry]
)
async def get_forecast_calendar(
    forecast_year: int,
    admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Get the 12-month operational calendar."""
    fc = await _get_latest_forecast(forecast_year, db)
    return [
        MonthCalendarEntry(
            month=m["month"],
            month_name=m["month_name"],
            demand_level=m["demand_level"],
            expected_demand=m["expected_demand"],
            confidence_range=f"{m['lower_bound']:.0f} – {m['upper_bound']:.0f}",
            status_label=m["status_label"],
            recommended_actions=m["recommended_actions"],
            key_factors=m["key_factors"],
        )
        for m in fc.monthly_forecasts
    ]


# ── Export endpoints ──


@router.get("/forecast/{forecast_year}/export.csv")
async def export_csv(
    forecast_year: int,
    admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Export forecast as CSV download."""
    fc = await _get_latest_forecast(forecast_year, db)
    # Rebuild calendar entries from stored data
    from services.reporting_service.services.seasonality.calendar_builder import (
        CalendarEntry,
    )

    cal = [
        CalendarEntry(
            month=m["month"],
            month_name=m["month_name"],
            demand_level=m["demand_level"],
            expected_demand=m["expected_demand"],
            lower_bound=m["lower_bound"],
            upper_bound=m["upper_bound"],
            seasonal_index=m["seasonal_index"],
            trend_factor=m["trend_factor"],
            status_label=m["status_label"],
            recommended_actions=m["recommended_actions"],
            key_factors=m["key_factors"],
        )
        for m in fc.monthly_forecasts
    ]
    csv_content = render_csv(cal)
    return StreamingResponse(
        iter([csv_content]),
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=SEASONALITY_CALENDAR_{forecast_year}.csv"
        },
    )


@router.get("/forecast/{forecast_year}/export.html")
async def export_html(
    forecast_year: int,
    admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Export forecast as self-contained HTML report."""
    fc = await _get_latest_forecast(forecast_year, db)
    from services.reporting_service.services.seasonality.calendar_builder import (
        CalendarEntry,
    )

    cal = [
        CalendarEntry(
            month=m["month"],
            month_name=m["month_name"],
            demand_level=m["demand_level"],
            expected_demand=m["expected_demand"],
            lower_bound=m["lower_bound"],
            upper_bound=m["upper_bound"],
            seasonal_index=m["seasonal_index"],
            trend_factor=m["trend_factor"],
            status_label=m["status_label"],
            recommended_actions=m["recommended_actions"],
            key_factors=m["key_factors"],
        )
        for m in fc.monthly_forecasts
    ]
    html = render_html(
        cal,
        forecast_year=fc.forecast_year,
        months_of_real_data=fc.months_of_real_data,
        prior_weight_pct=fc.prior_weight * 100,
    )
    return HTMLResponse(content=html)


@router.get("/forecast/{forecast_year}/export.md")
async def export_markdown(
    forecast_year: int,
    admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Export forecast as Markdown document."""
    fc = await _get_latest_forecast(forecast_year, db)
    from services.reporting_service.services.seasonality.calendar_builder import (
        CalendarEntry,
    )

    cal = [
        CalendarEntry(
            month=m["month"],
            month_name=m["month_name"],
            demand_level=m["demand_level"],
            expected_demand=m["expected_demand"],
            lower_bound=m["lower_bound"],
            upper_bound=m["upper_bound"],
            seasonal_index=m["seasonal_index"],
            trend_factor=m["trend_factor"],
            status_label=m["status_label"],
            recommended_actions=m["recommended_actions"],
            key_factors=m["key_factors"],
        )
        for m in fc.monthly_forecasts
    ]
    md = render_markdown(
        cal,
        forecast_year=fc.forecast_year,
        months_of_real_data=fc.months_of_real_data,
        prior_weight_pct=fc.prior_weight * 100,
        model_params=fc.model_params,
    )
    return PlainTextResponse(content=md, media_type="text/markdown")


# ── Actuals management ──


@router.post("/actuals/auto-ingest")
async def auto_ingest_actuals(
    year: int = Query(..., ge=2025, le=2035),
    admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Auto-ingest monthly actuals from live services for all past months of a year.

    Pulls data from attendance, sessions, payments, and members services.
    Only ingests completed months (not the current month).
    """
    from services.reporting_service.services.seasonality.ingest import (
        ingest_all_available_months,
    )

    ingested = await ingest_all_available_months(year, db)
    return {
        "year": year,
        "months_ingested": len(ingested),
        "months": [
            {
                "month": r.month,
                "attendance": r.total_attendance,
                "active_members": r.active_members,
            }
            for r in ingested
        ],
        "message": f"Ingested {len(ingested)} months from live services.",
    }


@router.get("/actuals", response_model=list[MonthlyActualResponse])
async def list_actuals(
    year: int = Query(..., ge=2025, le=2035),
    admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """List stored monthly actuals for a year."""
    result = await db.execute(
        select(MonthlyActual)
        .where(MonthlyActual.year == year)
        .order_by(MonthlyActual.month)
    )
    rows = result.scalars().all()
    return [
        MonthlyActualResponse(
            id=r.id,
            year=r.year,
            month=r.month,
            active_members=r.active_members,
            total_sessions_held=r.total_sessions_held,
            total_attendance=r.total_attendance,
            new_signups=r.new_signups,
            churned_members=r.churned_members,
            total_revenue_ngn=r.total_revenue_ngn,
            source=r.source.value if hasattr(r.source, "value") else r.source,
            created_at=r.created_at,
        )
        for r in rows
    ]


@router.post("/actuals/ingest", response_model=MonthlyActualResponse)
async def ingest_actual(
    body: IngestActualRequest,
    admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Manually enter or update monthly actual data."""
    # Upsert: check if record exists
    result = await db.execute(
        select(MonthlyActual).where(
            MonthlyActual.year == body.year,
            MonthlyActual.month == body.month,
        )
    )
    existing = result.scalar_one_or_none()

    if existing:
        existing.active_members = body.active_members
        existing.total_sessions_held = body.total_sessions_held
        existing.total_attendance = body.total_attendance
        existing.new_signups = body.new_signups
        existing.churned_members = body.churned_members
        existing.total_revenue_ngn = body.total_revenue_ngn
        existing.attendance_by_type = body.attendance_by_type
        existing.revenue_by_type = body.revenue_by_type
        existing.source = DataSource.MANUAL
        existing.computed_at = utc_now()
        record = existing
    else:
        record = MonthlyActual(
            year=body.year,
            month=body.month,
            active_members=body.active_members,
            total_sessions_held=body.total_sessions_held,
            total_attendance=body.total_attendance,
            new_signups=body.new_signups,
            churned_members=body.churned_members,
            total_revenue_ngn=body.total_revenue_ngn,
            attendance_by_type=body.attendance_by_type,
            revenue_by_type=body.revenue_by_type,
            source=DataSource.MANUAL,
        )
        db.add(record)

    await db.commit()
    await db.refresh(record)

    return MonthlyActualResponse(
        id=record.id,
        year=record.year,
        month=record.month,
        active_members=record.active_members,
        total_sessions_held=record.total_sessions_held,
        total_attendance=record.total_attendance,
        new_signups=record.new_signups,
        churned_members=record.churned_members,
        total_revenue_ngn=record.total_revenue_ngn,
        source=record.source.value
        if hasattr(record.source, "value")
        else record.source,
        created_at=record.created_at,
    )


# ── External factors ──


@router.post("/external-factors/seed")
async def seed_external_factors(
    body: SeedExternalFactorsRequest,
    admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Seed external factors for a year from Lagos priors."""
    year = body.year
    created = 0
    skipped = 0

    for month in range(1, 13):
        # Check if already exists
        result = await db.execute(
            select(ExternalFactor).where(
                ExternalFactor.year == year,
                ExternalFactor.month == month,
            )
        )
        if result.scalar_one_or_none():
            skipped += 1
            continue

        rain = LAGOS_MONTHLY_RAINFALL_MM.get(month, 0)
        school = LAGOS_SCHOOL_CALENDAR.get(month, {})
        holidays = LAGOS_PUBLIC_HOLIDAYS_2026.get(month, [])

        ef = ExternalFactor(
            year=year,
            month=month,
            rainfall_mm=rain,
            rainfall_category=rainfall_category(rain),
            school_term_active=school.get("term_active", True),
            exam_period=school.get("exam_period", False),
            school_holiday=school.get("holiday", False),
            holiday_names=holidays if holidays else None,
            holiday_count=len(holidays),
            salary_week=True,
            source=DataSource.PRIOR,
        )
        db.add(ef)
        created += 1

    await db.commit()

    return {
        "year": year,
        "created": created,
        "skipped": skipped,
        "message": f"Seeded {created} months, skipped {skipped} existing.",
    }
