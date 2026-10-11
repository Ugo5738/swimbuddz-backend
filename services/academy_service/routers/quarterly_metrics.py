"""Freeze Academy starts and recorded outcomes with the quarterly snapshot."""

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_service_role
from libs.auth.models import AuthUser
from libs.common.reporting import ReportingSection
from libs.db.session import get_async_db
from services.academy_service.routers.internal import (
    get_academy_quarter_summary_internal,
)

router = APIRouter(prefix="/internal/academy", tags=["internal"])


@router.get("/reporting-summary", response_model=ReportingSection)
async def get_reporting_summary(
    date_from: datetime = Query(..., alias="from"),
    date_to: datetime = Query(..., alias="to"),
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    summary = await get_academy_quarter_summary_internal(date_from, date_to, db)
    data = summary.model_dump()
    by_location = data.pop("by_location")
    return ReportingSection(
        metrics=data,
        details={"by_location": by_location},
        notes=[
            "Cohort starts and scheduled ends use cohort dates; an ended cohort is not evidence of graduation. Enrollment states are current state for cohorts overlapping the quarter.",
            "Certificates and progress updates use recorded event timestamps. Missing progress recording can understate learning outcomes.",
        ],
    )
