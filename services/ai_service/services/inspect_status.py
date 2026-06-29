"""Persist per-instance inspect job status inside AnalysisResult.coach_result."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.datetime_utils import utc_now
from services.ai_service.models import AnalysisResult


def inspect_key(aspect: str, instance_id: int) -> str:
    return f"{aspect}:{instance_id}"


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


async def set_inspect_status(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    aspect: str,
    instance_id: int,
    status: str,
    attempt: int = 1,
    message: str | None = None,
    next_retry_at: datetime | None = None,
    queue_depth: int | None = None,
    error_reason: str | None = None,
) -> dict:
    rs = await session.execute(
        select(AnalysisResult).where(AnalysisResult.job_id == job_id)
    )
    row = rs.scalar_one_or_none()
    if row is None:
        return {}
    cr = dict(row.coach_result or {})
    jobs = dict(cr.get("inspect_jobs") or {})
    key = inspect_key(aspect, instance_id)
    current = dict(jobs.get(key) or {})
    payload = {
        **current,
        "aspect": aspect,
        "instance_id": instance_id,
        "status": status,
        "attempt": attempt,
        "message": message,
        "next_retry_at": _iso(next_retry_at),
        "queue_depth": queue_depth,
        "error_reason": error_reason,
        "updated_at": _iso(utc_now()),
    }
    if "created_at" not in payload:
        payload["created_at"] = payload["updated_at"]
    jobs[key] = payload
    cr["inspect_jobs"] = jobs
    row.coach_result = cr
    return payload
