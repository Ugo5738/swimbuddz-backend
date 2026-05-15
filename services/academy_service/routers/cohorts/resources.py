"""Cohort resource listing endpoint."""

import uuid
from typing import List

from fastapi import APIRouter, Depends
from libs.auth.dependencies import require_coach, require_coach_for_cohort
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from services.academy_service.models import CohortResource
from services.academy_service.schemas import CohortResourceResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()


@router.get(
    "/cohorts/{cohort_id}/resources", response_model=List[CohortResourceResponse]
)
async def list_cohort_resources(
    cohort_id: uuid.UUID,
    current_user: AuthUser = Depends(require_coach),
    db: AsyncSession = Depends(get_async_db),
):
    """List resources for a specific cohort. Accessible by coach or admin."""
    await require_coach_for_cohort(current_user, str(cohort_id), db)

    query = (
        select(CohortResource)
        .where(CohortResource.cohort_id == cohort_id)
        .order_by(
            CohortResource.week_number.asc().nullsfirst(),
            CohortResource.created_at.asc(),
        )
    )
    result = await db.execute(query)
    return result.scalars().all()
