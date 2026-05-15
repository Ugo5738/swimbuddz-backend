"""Cohort CRUD endpoints (create / update / delete / get).

`get_cohort` (GET /cohorts/{cohort_id}) must be registered AFTER all the
static-path listing routes (/cohorts/open, /cohorts/enrollable, …) so
FastAPI doesn't capture the literal segment as a UUID. The aggregator's
include_router order ensures lists.router is registered first.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.common.config import get_settings
from libs.common.logging import get_logger
from libs.common.service_client import get_member_by_auth_id, internal_delete
from libs.db.session import get_async_db
from services.academy_service.models import CoachAssignment, Cohort
from services.academy_service.routers._shared import _ensure_active_coach
from services.academy_service.schemas import CohortCreate, CohortResponse, CohortUpdate
from services.academy_service.services.chat_sync import ensure_cohort_channel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

logger = get_logger(__name__)
router = APIRouter()


@router.post("/cohorts", response_model=CohortResponse)
async def create_cohort(
    cohort_in: CohortCreate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    # Validate all coaches listed in assignments are active
    for ca in cohort_in.coach_assignments or []:
        await _ensure_active_coach(ca.coach_id)

    # Extract coach_assignments before creating cohort (not a DB field)
    coach_assignments_input = cohort_in.coach_assignments
    cohort_data = cohort_in.model_dump(exclude={"coach_assignments"})
    cohort = Cohort(**cohort_data)
    db.add(cohort)
    await db.flush()  # Get cohort.id before creating assignments

    # Get admin member ID for assigned_by_id
    admin_member = await get_member_by_auth_id(
        current_user.user_id, calling_service="academy"
    )
    admin_id = admin_member["id"] if admin_member else None

    # Create CoachAssignment records
    for ca_input in coach_assignments_input or []:
        assignment = CoachAssignment(
            cohort_id=cohort.id,
            coach_id=ca_input.coach_id,
            role=ca_input.role,
            assigned_by_id=admin_id,
            status="active",
        )
        db.add(assignment)

        # Keep cohort.coach_id denormalised to the lead coach for fast lookups
        if ca_input.role == "lead":
            cohort.coach_id = ca_input.coach_id

    await db.commit()

    # Provision the cohort's chat channel (best-effort — never fails the
    # cohort create on chat downtime). The lead coach becomes channel admin
    # via `created_by`; other coach assignments come in through reconcile
    # when their enrollment-equivalent records exist (Phase 2).
    channel_id = await ensure_cohort_channel(
        cohort_id=cohort.id,
        cohort_name=cohort.name,
        created_by_member_id=cohort.coach_id,
        has_minors=False,
    )
    if channel_id is not None:
        logger.info("Chat channel %s provisioned for cohort %s", channel_id, cohort.id)

    query = (
        select(Cohort)
        .where(Cohort.id == cohort.id)
        .options(selectinload(Cohort.program))
    )
    result = await db.execute(query)
    return result.scalar_one()


@router.put("/cohorts/{cohort_id}", response_model=CohortResponse)
async def update_cohort(
    cohort_id: uuid.UUID,
    cohort_in: CohortUpdate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    query = (
        select(Cohort)
        .where(Cohort.id == cohort_id)
        .options(selectinload(Cohort.program))
    )
    result = await db.execute(query)
    cohort = result.scalar_one_or_none()

    if not cohort:
        raise HTTPException(status_code=404, detail="Cohort not found")

    update_data = cohort_in.model_dump(exclude_unset=True)

    for field, value in update_data.items():
        setattr(cohort, field, value)

    await db.commit()

    query = (
        select(Cohort)
        .where(Cohort.id == cohort.id)
        .options(selectinload(Cohort.program))
    )
    result = await db.execute(query)
    return result.scalar_one()


@router.delete("/cohorts/{cohort_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_cohort(
    cohort_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    query = select(Cohort).where(Cohort.id == cohort_id)
    result = await db.execute(query)
    cohort = result.scalar_one_or_none()

    if not cohort:
        raise HTTPException(status_code=404, detail="Cohort not found")

    # Clean up sessions via sessions-service (cross-service, no FK cascade).

    settings = get_settings()
    resp = await internal_delete(
        service_url=settings.SESSIONS_SERVICE_URL,
        path=f"/sessions/by-cohort/{cohort_id}",
        calling_service="academy",
        timeout=15,
    )
    if not resp.is_success:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to delete cohort sessions",
        )

    # DB cascades handle: enrollments → student_progress, cohort_resources,
    # cohort_complexity_scores, coach_assignments (all have ondelete="CASCADE").
    await db.delete(cohort)
    await db.commit()
    return None


@router.get("/cohorts/{cohort_id}", response_model=CohortResponse)
async def get_cohort(
    cohort_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    query = (
        select(Cohort)
        .where(Cohort.id == cohort_id)
        .options(selectinload(Cohort.program))
    )
    result = await db.execute(query)
    cohort = result.scalar_one_or_none()
    if not cohort:
        raise HTTPException(status_code=404, detail="Cohort not found")
    return cohort
