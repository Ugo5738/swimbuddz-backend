"""Curriculum-week CRUD + reorder within a curriculum."""

"""
Curriculum and Skills CRUD endpoints.

This router handles:
- Skills library (global, reusable skills)
- Program curriculum (weeks, lessons, skill tagging)
- Sync to Program.curriculum_json after mutations
"""

import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from services.academy_service.models import (
    CurriculumLesson,
    CurriculumWeek,
    LessonSkill,
    ProgramCurriculum,
)
from services.academy_service.schemas.curriculum import (
    CurriculumWeekCreate,
    CurriculumWeekResponse,
    CurriculumWeekUpdate,
)
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ._helpers import get_program_id_from_week, sync_curriculum_json


router = APIRouter()


@router.post("/curricula/{curriculum_id}/weeks", response_model=CurriculumWeekResponse)
async def add_week(
    curriculum_id: uuid.UUID,
    week_in: CurriculumWeekCreate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Add a week to a curriculum."""
    # Check curriculum exists
    curr_query = select(ProgramCurriculum).where(ProgramCurriculum.id == curriculum_id)
    curr_result = await db.execute(curr_query)
    curriculum = curr_result.scalar_one_or_none()

    if not curriculum:
        raise HTTPException(status_code=404, detail="Curriculum not found")

    # Get max order_index
    max_query = select(CurriculumWeek.order_index).where(
        CurriculumWeek.curriculum_id == curriculum_id
    )
    max_result = await db.execute(max_query)
    existing_indices = max_result.scalars().all()
    next_index = max(existing_indices) + 1 if existing_indices else 0

    week = CurriculumWeek(
        curriculum_id=curriculum_id,
        week_number=week_in.week_number,
        theme=week_in.theme,
        objectives=week_in.objectives,
        order_index=next_index,
    )
    db.add(week)
    await db.commit()
    await db.refresh(week)

    # Sync JSON
    await sync_curriculum_json(db, curriculum.program_id)

    return CurriculumWeekResponse(
        id=week.id,
        curriculum_id=week.curriculum_id,
        week_number=week.week_number,
        theme=week.theme,
        objectives=week.objectives,
        order_index=week.order_index,
        created_at=week.created_at,
        lessons=[],
    )


@router.put("/curriculum-weeks/{week_id}", response_model=CurriculumWeekResponse)
async def update_week(
    week_id: uuid.UUID,
    week_in: CurriculumWeekUpdate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Update a curriculum week."""
    query = select(CurriculumWeek).where(CurriculumWeek.id == week_id)
    result = await db.execute(query)
    week = result.scalar_one_or_none()

    if not week:
        raise HTTPException(status_code=404, detail="Week not found")

    update_data = week_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(week, field, value)

    await db.commit()
    await db.refresh(week)

    # Get program_id and sync JSON
    program_id = await get_program_id_from_week(db, week_id)
    await sync_curriculum_json(db, program_id)

    # Reload with lessons
    query = (
        select(CurriculumWeek)
        .where(CurriculumWeek.id == week_id)
        .options(selectinload(CurriculumWeek.lessons))
    )
    result = await db.execute(query)
    week = result.scalar_one()

    return week


@router.delete("/curriculum-weeks/{week_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_week(
    week_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Delete a curriculum week and all its lessons."""
    # Get program_id before deletion
    program_id = await get_program_id_from_week(db, week_id)

    query = select(CurriculumWeek).where(CurriculumWeek.id == week_id)
    result = await db.execute(query)
    week = result.scalar_one_or_none()

    if not week:
        raise HTTPException(status_code=404, detail="Week not found")

    # Delete lesson skills first
    lesson_ids_query = select(CurriculumLesson.id).where(
        CurriculumLesson.week_id == week_id
    )
    lesson_ids_result = await db.execute(lesson_ids_query)
    lesson_ids = lesson_ids_result.scalars().all()

    if lesson_ids:
        await db.execute(
            delete(LessonSkill).where(LessonSkill.lesson_id.in_(lesson_ids))
        )

    # Delete lessons
    await db.execute(
        delete(CurriculumLesson).where(CurriculumLesson.week_id == week_id)
    )

    # Delete week
    await db.delete(week)
    await db.commit()

    # Sync JSON
    await sync_curriculum_json(db, program_id)

    return None


@router.put("/curricula/{curriculum_id}/weeks/reorder")
async def reorder_weeks(
    curriculum_id: uuid.UUID,
    week_ids: List[uuid.UUID],
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Reorder weeks by providing the week IDs in the desired order."""
    # Verify curriculum exists
    curr_query = select(ProgramCurriculum).where(ProgramCurriculum.id == curriculum_id)
    curr_result = await db.execute(curr_query)
    curriculum = curr_result.scalar_one_or_none()

    if not curriculum:
        raise HTTPException(status_code=404, detail="Curriculum not found")

    # Update order_index for each week
    for index, week_id in enumerate(week_ids):
        week_query = select(CurriculumWeek).where(
            CurriculumWeek.id == week_id, CurriculumWeek.curriculum_id == curriculum_id
        )
        result = await db.execute(week_query)
        week = result.scalar_one_or_none()
        if week:
            week.order_index = index

    await db.commit()

    # Sync JSON
    await sync_curriculum_json(db, curriculum.program_id)

    return {"message": "Weeks reordered successfully"}
