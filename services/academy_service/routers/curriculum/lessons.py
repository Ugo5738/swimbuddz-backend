"""Curriculum-lesson CRUD + reorder within a week."""

"""
Curriculum and Skills CRUD endpoints.

This router handles:
- Skills library (global, reusable skills)
- Program curriculum (weeks, lessons, skill tagging)
- Sync to Program.curriculum_json after mutations
"""

import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.common.media_utils import resolve_media_urls
from libs.db.session import get_async_db
from services.academy_service.models import (
    CurriculumLesson,
    CurriculumWeek,
    LessonSkill,
    Program,
    ProgramCurriculum,
    Skill,
)
from services.academy_service.schemas.curriculum import (
    CurriculumLessonCreate,
    CurriculumLessonResponse,
    CurriculumLessonUpdate,
    CurriculumWeekCreate,
    CurriculumWeekResponse,
    CurriculumWeekUpdate,
    ProgramCurriculumResponse,
    SkillCreate,
    SkillResponse,
    SkillUpdate,
)
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ._helpers import get_program_id_from_lesson, get_program_id_from_week, sync_curriculum_json



router = APIRouter()


@router.post(
    "/curriculum-weeks/{week_id}/lessons", response_model=CurriculumLessonResponse
)
async def add_lesson(
    week_id: uuid.UUID,
    lesson_in: CurriculumLessonCreate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Add a lesson to a week."""
    # Check week exists
    week_query = select(CurriculumWeek).where(CurriculumWeek.id == week_id)
    week_result = await db.execute(week_query)
    week = week_result.scalar_one_or_none()

    if not week:
        raise HTTPException(status_code=404, detail="Week not found")

    # Get max order_index
    max_query = select(CurriculumLesson.order_index).where(
        CurriculumLesson.week_id == week_id
    )
    max_result = await db.execute(max_query)
    existing_indices = max_result.scalars().all()
    next_index = max(existing_indices) + 1 if existing_indices else 0

    lesson = CurriculumLesson(
        week_id=week_id,
        title=lesson_in.title,
        description=lesson_in.description,
        duration_minutes=lesson_in.duration_minutes,
        video_url=lesson_in.video_url,
        order_index=next_index,
    )
    db.add(lesson)
    await db.commit()
    await db.refresh(lesson)

    # Add skill tags if provided
    skills = []
    if lesson_in.skill_ids:
        for skill_id in lesson_in.skill_ids:
            # Verify skill exists
            skill_query = select(Skill).where(Skill.id == skill_id)
            skill_result = await db.execute(skill_query)
            skill = skill_result.scalar_one_or_none()
            if skill:
                lesson_skill = LessonSkill(lesson_id=lesson.id, skill_id=skill_id)
                db.add(lesson_skill)
                skills.append(
                    SkillResponse(
                        id=skill.id,
                        name=skill.name,
                        category=skill.category,
                        description=skill.description,
                        created_at=skill.created_at,
                    )
                )
        await db.commit()

    # Sync JSON
    program_id = await get_program_id_from_week(db, week_id)
    await sync_curriculum_json(db, program_id)

    return CurriculumLessonResponse(
        id=lesson.id,
        week_id=lesson.week_id,
        title=lesson.title,
        description=lesson.description,
        duration_minutes=lesson.duration_minutes,
        video_url=lesson.video_url,
        order_index=lesson.order_index,
        created_at=lesson.created_at,
        skills=skills,
    )


@router.put("/curriculum-lessons/{lesson_id}", response_model=CurriculumLessonResponse)
async def update_lesson(
    lesson_id: uuid.UUID,
    lesson_in: CurriculumLessonUpdate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Update a curriculum lesson."""
    query = select(CurriculumLesson).where(CurriculumLesson.id == lesson_id)
    result = await db.execute(query)
    lesson = result.scalar_one_or_none()

    if not lesson:
        raise HTTPException(status_code=404, detail="Lesson not found")

    # Update basic fields
    update_data = lesson_in.model_dump(exclude_unset=True, exclude={"skill_ids"})
    for field, value in update_data.items():
        setattr(lesson, field, value)

    await db.commit()

    # Update skill tags if provided
    if lesson_in.skill_ids is not None:
        # Clear existing skill links
        await db.execute(delete(LessonSkill).where(LessonSkill.lesson_id == lesson_id))

        # Add new skill links
        for skill_id in lesson_in.skill_ids:
            skill_query = select(Skill).where(Skill.id == skill_id)
            skill_result = await db.execute(skill_query)
            if skill_result.scalar_one_or_none():
                lesson_skill = LessonSkill(lesson_id=lesson_id, skill_id=skill_id)
                db.add(lesson_skill)

        await db.commit()

    # Sync JSON
    program_id = await get_program_id_from_lesson(db, lesson_id)
    await sync_curriculum_json(db, program_id)

    # Reload with skills
    await db.refresh(lesson)

    # Get skills for response
    skill_query = (
        select(Skill)
        .join(LessonSkill, LessonSkill.skill_id == Skill.id)
        .where(LessonSkill.lesson_id == lesson_id)
    )
    skill_result = await db.execute(skill_query)
    skills = [
        SkillResponse(
            id=s.id,
            name=s.name,
            category=s.category,
            description=s.description,
            created_at=s.created_at,
        )
        for s in skill_result.scalars().all()
    ]

    return CurriculumLessonResponse(
        id=lesson.id,
        week_id=lesson.week_id,
        title=lesson.title,
        description=lesson.description,
        duration_minutes=lesson.duration_minutes,
        video_url=lesson.video_url,
        order_index=lesson.order_index,
        created_at=lesson.created_at,
        skills=skills,
    )


@router.delete(
    "/curriculum-lessons/{lesson_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_lesson(
    lesson_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Delete a curriculum lesson."""
    # Get program_id before deletion
    program_id = await get_program_id_from_lesson(db, lesson_id)

    query = select(CurriculumLesson).where(CurriculumLesson.id == lesson_id)
    result = await db.execute(query)
    lesson = result.scalar_one_or_none()

    if not lesson:
        raise HTTPException(status_code=404, detail="Lesson not found")

    # Delete skill links first
    await db.execute(delete(LessonSkill).where(LessonSkill.lesson_id == lesson_id))

    # Delete lesson
    await db.delete(lesson)
    await db.commit()

    # Sync JSON
    await sync_curriculum_json(db, program_id)

    return None


@router.put("/curriculum-weeks/{week_id}/lessons/reorder")
async def reorder_lessons(
    week_id: uuid.UUID,
    lesson_ids: List[uuid.UUID],
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Reorder lessons within a week by providing the lesson IDs in the desired order."""
    # Verify week exists
    week_query = select(CurriculumWeek).where(CurriculumWeek.id == week_id)
    week_result = await db.execute(week_query)
    week = week_result.scalar_one_or_none()

    if not week:
        raise HTTPException(status_code=404, detail="Week not found")

    # Update order_index for each lesson
    for index, lesson_id in enumerate(lesson_ids):
        lesson_query = select(CurriculumLesson).where(
            CurriculumLesson.id == lesson_id, CurriculumLesson.week_id == week_id
        )
        result = await db.execute(lesson_query)
        lesson = result.scalar_one_or_none()
        if lesson:
            lesson.order_index = index

    await db.commit()

    # Sync JSON
    program_id = await get_program_id_from_week(db, week_id)
    await sync_curriculum_json(db, program_id)

    return {"message": "Lessons reordered successfully"}
