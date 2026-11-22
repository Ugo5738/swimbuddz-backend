"""Admin-only programme/module/cohort assignments and frozen milestone requirements.

No endpoint in this router mutates StudentProgress, graduation, class balances
or Club eligibility. The immutable manifest is a sidecar until parity is proven.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.common.datetime_utils import utc_now
from libs.db.session import get_async_db
from services.academy_service.models import (
    AcademyCohortMilestoneAssignment,
    AcademyCohortRequirementsSnapshot,
    AcademyMilestoneVersion,
    AcademyModuleMilestone,
    AcademyModuleVersion,
    AcademyProgramMilestoneAssignment,
    AcademyProgramModule,
    Cohort,
    Program,
)
from services.academy_service.services.requirements_resolver import (
    build_cohort_requirements,
    requirements_sha256,
)

router = APIRouter(
    prefix="/admin",
    tags=["academy-requirements"],
    dependencies=[Depends(require_admin)],
)


class ModuleMilestoneInput(BaseModel):
    milestone_version_id: uuid.UUID
    required_for_completion: bool = True
    order_index: int = Field(default=0, ge=0)


class ProgramModuleInput(BaseModel):
    module_version_id: uuid.UUID
    order_index: int = Field(ge=0)


class ProgramMilestoneInput(BaseModel):
    milestone_version_id: uuid.UUID
    required_for_completion: bool = True
    target_week: int | None = Field(default=None, ge=1)


class CohortMilestoneInput(BaseModel):
    milestone_version_id: uuid.UUID
    target_week: int | None = Field(default=None, ge=1)
    override_reason: str = Field(min_length=10, max_length=2000)


def _published(entity, label: str) -> None:
    if entity is None or entity.state != "published" or entity.published_at is None:
        raise HTTPException(status_code=409, detail=f"{label} must be published")


async def _program_for_update(db: AsyncSession, program_id: uuid.UUID) -> Program:
    item = (
        await db.execute(
            select(Program).where(Program.id == program_id).with_for_update()
        )
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="Program not found")
    return item


async def _assert_not_frozen(db: AsyncSession, cohort_id: uuid.UUID) -> None:
    frozen = await db.scalar(
        select(AcademyCohortRequirementsSnapshot.id).where(
            AcademyCohortRequirementsSnapshot.cohort_id == cohort_id
        )
    )
    if frozen is not None:
        raise HTTPException(
            status_code=409,
            detail="Cohort requirements are frozen; create an audited amendment instead",
        )


async def _commit_assignment(db: AsyncSession, row) -> dict:
    db.add(row)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Assignment conflicts with an existing version or order",
        ) from exc
    return {"id": str(row.id)}


@router.post("/modules/{module_version_id}/curriculum/milestones", status_code=201)
async def assign_module_milestone(
    module_version_id: uuid.UUID,
    payload: ModuleMilestoneInput,
    db: AsyncSession = Depends(get_async_db),
):
    module = (
        await db.execute(
            select(AcademyModuleVersion)
            .where(AcademyModuleVersion.id == module_version_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if module is None:
        raise HTTPException(status_code=404, detail="Module version not found")
    if module.state != "draft":
        raise HTTPException(
            status_code=409, detail="Published module requirements cannot be changed"
        )

    milestone = await db.get(AcademyMilestoneVersion, payload.milestone_version_id)
    _published(milestone, "Milestone version")
    if milestone.safety_gate and not payload.required_for_completion:
        raise HTTPException(status_code=409, detail="Safety gates must be required")

    return await _commit_assignment(
        db,
        AcademyModuleMilestone(
            module_version_id=module_version_id,
            milestone_version_id=payload.milestone_version_id,
            required_for_completion=payload.required_for_completion,
            order_index=payload.order_index,
        ),
    )


@router.post("/programs/{program_id}/curriculum/modules", status_code=201)
async def assign_program_module(
    program_id: uuid.UUID,
    payload: ProgramModuleInput,
    db: AsyncSession = Depends(get_async_db),
):
    await _program_for_update(db, program_id)
    module = await db.get(AcademyModuleVersion, payload.module_version_id)
    _published(module, "Module version")

    return await _commit_assignment(
        db,
        AcademyProgramModule(
            program_id=program_id,
            module_version_id=payload.module_version_id,
            order_index=payload.order_index,
        ),
    )


@router.post("/programs/{program_id}/curriculum/milestones", status_code=201)
async def assign_program_milestone(
    program_id: uuid.UUID,
    payload: ProgramMilestoneInput,
    db: AsyncSession = Depends(get_async_db),
):
    await _program_for_update(db, program_id)
    milestone = await db.get(AcademyMilestoneVersion, payload.milestone_version_id)
    _published(milestone, "Milestone version")
    if milestone.safety_gate and not payload.required_for_completion:
        raise HTTPException(status_code=409, detail="Safety gates must be required")

    return await _commit_assignment(
        db,
        AcademyProgramMilestoneAssignment(
            program_id=program_id,
            milestone_version_id=payload.milestone_version_id,
            target_week=payload.target_week,
            required_for_completion=payload.required_for_completion,
        ),
    )


@router.post("/cohorts/{cohort_id}/curriculum/milestones", status_code=201)
async def assign_cohort_milestone(
    cohort_id: uuid.UUID,
    payload: CohortMilestoneInput,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    cohort = await db.get(Cohort, cohort_id)
    if cohort is None:
        raise HTTPException(status_code=404, detail="Cohort not found")

    await _program_for_update(db, cohort.program_id)
    cohort = (
        await db.execute(select(Cohort).where(Cohort.id == cohort_id).with_for_update())
    ).scalar_one()
    await _assert_not_frozen(db, cohort.id)

    milestone = await db.get(AcademyMilestoneVersion, payload.milestone_version_id)
    _published(milestone, "Milestone version")

    return await _commit_assignment(
        db,
        AcademyCohortMilestoneAssignment(
            cohort_id=cohort_id,
            milestone_version_id=payload.milestone_version_id,
            target_week=payload.target_week,
            override_reason=payload.override_reason.strip(),
            assigned_by_auth_id=str(current_user.user_id),
        ),
    )


@router.get("/cohorts/{cohort_id}/curriculum/requirements/preview")
async def preview_cohort_requirements(
    cohort_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    cohort = await db.get(Cohort, cohort_id)
    if cohort is None:
        raise HTTPException(status_code=404, detail="Cohort not found")
    result = await build_cohort_requirements(db, cohort)
    return {"sha256": requirements_sha256(result), "requirements": result}


@router.get("/cohorts/{cohort_id}/curriculum/requirements/frozen")
async def get_frozen_cohort_requirements(
    cohort_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    snapshot = await db.scalar(
        select(AcademyCohortRequirementsSnapshot).where(
            AcademyCohortRequirementsSnapshot.cohort_id == cohort_id
        )
    )
    if snapshot is None:
        raise HTTPException(status_code=404, detail="No frozen requirements for cohort")
    return _snapshot_response(snapshot)


def _snapshot_response(snapshot: AcademyCohortRequirementsSnapshot) -> dict:
    return {
        "id": str(snapshot.id),
        "sha256": snapshot.sha256,
        "requirements": snapshot.requirements_json,
        "created_by_auth_id": snapshot.created_by_auth_id,
        "created_at": snapshot.created_at,
    }


@router.post("/cohorts/{cohort_id}/curriculum/requirements/freeze", status_code=201)
async def freeze_cohort_requirements(
    cohort_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    cohort = await db.get(Cohort, cohort_id)
    if cohort is None:
        raise HTTPException(status_code=404, detail="Cohort not found")

    # Synchronize with program-level assignments and cohort-level overrides.
    await _program_for_update(db, cohort.program_id)
    locked_cohort = (
        await db.execute(select(Cohort).where(Cohort.id == cohort_id).with_for_update())
    ).scalar_one()

    existing = await db.scalar(
        select(AcademyCohortRequirementsSnapshot).where(
            AcademyCohortRequirementsSnapshot.cohort_id == cohort_id
        )
    )
    if existing is not None:
        # Idempotent: once issued, the same manifest is returned unchanged,
        # even when later programme defaults have evolved.
        return _snapshot_response(existing)

    requirements = await build_cohort_requirements(db, locked_cohort)
    snapshot = AcademyCohortRequirementsSnapshot(
        cohort_id=cohort_id,
        program_id=cohort.program_id,
        requirements_json=requirements,
        sha256=requirements_sha256(requirements),
        created_by_auth_id=str(current_user.user_id),
        created_at=utc_now(),
    )
    db.add(snapshot)
    await db.commit()
    await db.refresh(snapshot)
    return _snapshot_response(snapshot)
