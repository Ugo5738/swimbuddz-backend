"""Internal Stroke Lab summary endpoint for Member Swim Scorecard (ADR-026).

Returns completed member analysis metrics (stroke rate, body roll proxy,
breath balance, focus area, short summary) without exposing raw video storage
paths or signed URLs.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_service_role
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from services.ai_service.models import (
    AnalysisJob,
    AnalysisJobSource,
    AnalysisJobStatus,
    AnalysisResult,
)

router = APIRouter(prefix="/internal/ai", tags=["internal"])


@router.get("/members/{member_auth_id}/strokelab-summary")
async def get_member_strokelab_summary(
    member_auth_id: str,
    limit: int = Query(default=20, ge=1, le=50),
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Return completed Stroke Lab metric snapshots for one member."""
    try:
        auth_uuid = uuid.UUID(member_auth_id)
    except ValueError:
        return {"total_analyses": 0, "recent_runs": []}

    rows = (
        await db.execute(
            select(AnalysisJob, AnalysisResult)
            .join(AnalysisResult, AnalysisResult.job_id == AnalysisJob.id)
            .where(
                AnalysisJob.member_auth_id == auth_uuid,
                AnalysisJob.source == AnalysisJobSource.MEMBER,
                AnalysisJob.status == AnalysisJobStatus.COMPLETED,
            )
            .order_by(
                AnalysisJob.completed_at.desc().nullslast(),
                AnalysisJob.created_at.desc(),
            )
            .limit(limit)
        )
    ).all()

    recent_runs = []
    for job, result in rows:
        completed_at = job.completed_at or result.created_at or job.created_at
        recent_runs.append(
            {
                "job_id": str(job.id),
                "stroke_type": result.detected_stroke or job.stroke_type,
                "completed_at": completed_at.isoformat(),
                "stroke_rate_spm": result.stroke_rate_spm,
                "body_roll_proxy_degrees": result.body_roll_proxy_degrees,
                "breath_balance_left_ratio": result.breath_balance_left_ratio,
                "focus_area": job.focus_area,
                "summary_text": result.summary_text,
            }
        )

    return {
        "total_analyses": len(recent_runs),
        "recent_runs": recent_runs,
    }
