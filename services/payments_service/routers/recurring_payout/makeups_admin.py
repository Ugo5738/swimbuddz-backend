"""Admin-only make-up obligation endpoints (list / schedule / cancel)."""

from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.db.config import AsyncSessionLocal
from services.payments_service.models import CohortMakeupObligation, MakeupStatus
from services.payments_service.schemas import (
    MakeupObligationListResponse,
    MakeupObligationResponse,
    MakeupScheduleRequest,
)
from sqlalchemy import func, select

router = APIRouter()


@router.get("/", response_model=MakeupObligationListResponse)
async def list_makeup_obligations(
    cohort_id: Optional[uuid.UUID] = None,
    coach_member_id: Optional[uuid.UUID] = None,
    student_member_id: Optional[uuid.UUID] = None,
    status_filter: Optional[MakeupStatus] = Query(default=None, alias="status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    _admin: AuthUser = Depends(require_admin),
):
    async with AsyncSessionLocal() as db:
        stmt = select(CohortMakeupObligation)
        count_stmt = select(func.count()).select_from(CohortMakeupObligation)
        if cohort_id:
            stmt = stmt.where(CohortMakeupObligation.cohort_id == cohort_id)
            count_stmt = count_stmt.where(CohortMakeupObligation.cohort_id == cohort_id)
        if coach_member_id:
            stmt = stmt.where(CohortMakeupObligation.coach_member_id == coach_member_id)
            count_stmt = count_stmt.where(
                CohortMakeupObligation.coach_member_id == coach_member_id
            )
        if student_member_id:
            stmt = stmt.where(
                CohortMakeupObligation.student_member_id == student_member_id
            )
            count_stmt = count_stmt.where(
                CohortMakeupObligation.student_member_id == student_member_id
            )
        if status_filter:
            stmt = stmt.where(CohortMakeupObligation.status == status_filter)
            count_stmt = count_stmt.where(
                CohortMakeupObligation.status == status_filter
            )

        total = (await db.execute(count_stmt)).scalar_one()
        result = await db.execute(
            stmt.order_by(CohortMakeupObligation.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        items = [
            MakeupObligationResponse.model_validate(row)
            for row in result.scalars().all()
        ]
        return MakeupObligationListResponse(items=items, total=total)


@router.patch("/{obligation_id}/schedule", response_model=MakeupObligationResponse)
async def admin_schedule_makeup(
    obligation_id: uuid.UUID,
    payload: MakeupScheduleRequest,
    _admin: AuthUser = Depends(require_admin),
):
    """Admin override to schedule a make-up to a specific session.

    Coaches use a separate coach-facing endpoint for the same operation
    on their own cohorts.
    """
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(CohortMakeupObligation).where(
                CohortMakeupObligation.id == obligation_id
            )
        )
        obligation = result.scalar_one_or_none()
        if not obligation:
            raise HTTPException(status_code=404, detail="Obligation not found")
        if obligation.status not in (MakeupStatus.PENDING, MakeupStatus.SCHEDULED):
            raise HTTPException(
                status_code=400,
                detail=f"Cannot reschedule obligation in status {obligation.status.value}",
            )

        obligation.scheduled_session_id = payload.scheduled_session_id
        obligation.status = MakeupStatus.SCHEDULED
        if payload.notes:
            obligation.notes = payload.notes
        await db.commit()
        await db.refresh(obligation)
        return MakeupObligationResponse.model_validate(obligation)


@router.patch("/{obligation_id}/cancel", response_model=MakeupObligationResponse)
async def admin_cancel_makeup(
    obligation_id: uuid.UUID,
    _admin: AuthUser = Depends(require_admin),
):
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(CohortMakeupObligation).where(
                CohortMakeupObligation.id == obligation_id
            )
        )
        obligation = result.scalar_one_or_none()
        if not obligation:
            raise HTTPException(status_code=404, detail="Obligation not found")
        if obligation.status == MakeupStatus.COMPLETED:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Cannot cancel a completed make-up",
            )
        obligation.status = MakeupStatus.CANCELLED
        await db.commit()
        await db.refresh(obligation)
        return MakeupObligationResponse.model_validate(obligation)
