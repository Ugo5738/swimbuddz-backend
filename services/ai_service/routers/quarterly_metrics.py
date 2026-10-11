"""Stroke Lab submission/completion counts without personal analysis data."""

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_service_role
from libs.auth.models import AuthUser
from libs.common.reporting import ReportingSection, enum_value, reporting_window
from libs.db.session import get_async_db
from services.ai_service.models.analysis import AnalysisJob, AnalysisJobStatus

router = APIRouter(prefix="/internal/ai", tags=["internal"])


@router.get("/reporting-summary", response_model=ReportingSection)
async def get_reporting_summary(
    date_from: datetime = Query(..., alias="from"),
    date_to: datetime = Query(..., alias="to"),
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    start, end = reporting_window(date_from, date_to)
    rows = (
        await db.execute(
            select(AnalysisJob.source, AnalysisJob.status, func.count(AnalysisJob.id))
            .where(
                AnalysisJob.created_at >= start,
                AnalysisJob.created_at < end,
            )
            .group_by(AnalysisJob.source, AnalysisJob.status)
        )
    ).all()
    completions = (
        await db.execute(
            select(func.count(AnalysisJob.id)).where(
                AnalysisJob.completed_at >= start,
                AnalysisJob.completed_at < end,
                AnalysisJob.status == AnalysisJobStatus.COMPLETED,
            )
        )
    ).scalar_one()
    breakdown = {
        f"{enum_value(source)}_{enum_value(status)}": count
        for source, status, count in rows
    }
    return ReportingSection(
        metrics={
            "strokelab_submissions": sum(count for _, _, count in rows),
            "strokelab_completed_in_period": completions,
            "strokelab_public_submissions": sum(
                count for source, _, count in rows if enum_value(source) == "public"
            ),
        },
        details={"submission_status": breakdown},
        notes=[
            "Submissions use created_at and current status. Completions use completed_at; no guest emails, tokens, videos or analysis results are exposed.",
        ],
    )
