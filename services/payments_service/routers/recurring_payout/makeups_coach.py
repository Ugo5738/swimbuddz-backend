"""Coach-facing make-up obligation endpoints (list / schedule)."""

from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from libs.auth.dependencies import get_current_user
from libs.auth.models import AuthUser
from libs.common.datetime_utils import utc_now
from libs.db.config import AsyncSessionLocal
from services.payments_service.models import CohortMakeupObligation, MakeupStatus
from services.payments_service.schemas import (
    MakeupObligationListResponse,
    MakeupObligationResponse,
    MakeupScheduleRequest,
)
from sqlalchemy import func, select, text

from ._helpers import _resolve_coach_member_id

router = APIRouter()


@router.get("/", response_model=MakeupObligationListResponse)
async def coach_list_makeup_obligations(
    cohort_id: Optional[uuid.UUID] = None,
    status_filter: Optional[MakeupStatus] = Query(default=None, alias="status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current_user: AuthUser = Depends(get_current_user),
):
    """List make-up obligations owned by the calling coach.

    Filtered server-side by `coach_member_id == current_user.member_id`,
    so coaches can never see another coach's obligations.
    """
    coach_member_id = await _resolve_coach_member_id(current_user)

    async with AsyncSessionLocal() as db:
        stmt = select(CohortMakeupObligation).where(
            CohortMakeupObligation.coach_member_id == coach_member_id,
        )
        count_stmt = (
            select(func.count())
            .select_from(CohortMakeupObligation)
            .where(CohortMakeupObligation.coach_member_id == coach_member_id)
        )
        if cohort_id:
            stmt = stmt.where(CohortMakeupObligation.cohort_id == cohort_id)
            count_stmt = count_stmt.where(CohortMakeupObligation.cohort_id == cohort_id)
        if status_filter:
            stmt = stmt.where(CohortMakeupObligation.status == status_filter)
            count_stmt = count_stmt.where(
                CohortMakeupObligation.status == status_filter
            )

        total = (await db.execute(count_stmt)).scalar_one()
        result = await db.execute(
            stmt.order_by(CohortMakeupObligation.created_at.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        items = [
            MakeupObligationResponse.model_validate(row)
            for row in result.scalars().all()
        ]
        return MakeupObligationListResponse(items=items, total=total)


@router.patch("/{obligation_id}/schedule", response_model=MakeupObligationResponse)
async def coach_schedule_makeup(
    obligation_id: uuid.UUID,
    payload: MakeupScheduleRequest,
    current_user: AuthUser = Depends(get_current_user),
):
    """Coach links a queued obligation to one of their cohort sessions.

    Verifications:
      - Caller is the coach who owns the obligation.
      - The target session belongs to the same cohort as the obligation.
      - The session has not yet started (can't schedule a make-up to the past).
      - The obligation is in PENDING or SCHEDULED state (allows reschedule
        before the make-up is delivered).
    """
    coach_member_id = await _resolve_coach_member_id(current_user)

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(CohortMakeupObligation).where(
                CohortMakeupObligation.id == obligation_id
            )
        )
        obligation = result.scalar_one_or_none()
        if not obligation:
            raise HTTPException(status_code=404, detail="Obligation not found")
        if obligation.coach_member_id != coach_member_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not your obligation",
            )
        if obligation.status not in (MakeupStatus.PENDING, MakeupStatus.SCHEDULED):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Cannot schedule obligation in status "
                    f"{obligation.status.value}"
                ),
            )

        # Verify the target session is in the same cohort and is in the future.
        session_row = (
            (
                await db.execute(
                    text(
                        """
                    SELECT id, cohort_id, starts_at, status
                    FROM public.sessions
                    WHERE id = :sid
                    """
                    ),
                    {"sid": payload.scheduled_session_id},
                )
            )
            .mappings()
            .first()
        )
        if not session_row:
            raise HTTPException(status_code=404, detail="Target session not found")
        if session_row["cohort_id"] != obligation.cohort_id:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Target session belongs to a different cohort than the "
                    "obligation"
                ),
            )
        starts_at = session_row["starts_at"]
        if starts_at is not None and starts_at <= utc_now():
            raise HTTPException(
                status_code=400,
                detail=("Target session has already started; pick a future session"),
            )

        obligation.scheduled_session_id = payload.scheduled_session_id
        obligation.status = MakeupStatus.SCHEDULED
        if payload.notes:
            obligation.notes = payload.notes
        await db.commit()
        await db.refresh(obligation)
        return MakeupObligationResponse.model_validate(obligation)
