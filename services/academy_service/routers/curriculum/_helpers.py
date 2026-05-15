"""Helper functions shared across curriculum sub-routers."""

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

async def sync_curriculum_json(db: AsyncSession, program_id: uuid.UUID) -> None:
    """
    Regenerate Program.curriculum_json from normalized tables.
    Called after every curriculum mutation to keep both storages in sync.
    """
    # Get active curriculum with weeks and lessons
    query = (
        select(ProgramCurriculum)
        .where(ProgramCurriculum.program_id == program_id)
        .where(ProgramCurriculum.is_active.is_(True))
        .options(
            selectinload(ProgramCurriculum.weeks).selectinload(CurriculumWeek.lessons)
        )
    )
    result = await db.execute(query)
    curriculum = result.scalar_one_or_none()

    if not curriculum:
        # No curriculum - clear the JSON
        program_query = select(Program).where(Program.id == program_id)
        program_result = await db.execute(program_query)
        program = program_result.scalar_one_or_none()
        if program:
            program.curriculum_json = None
            await db.commit()
        return

    # Build JSON structure
    weeks_json = []
    for week in sorted(curriculum.weeks, key=lambda w: w.order_index):
        lessons_json = []
        for lesson in sorted(week.lessons, key=lambda les: les.order_index):
            # Get skill IDs for this lesson
            skill_query = select(LessonSkill.skill_id).where(
                LessonSkill.lesson_id == lesson.id
            )
            skill_result = await db.execute(skill_query)
            skill_ids = [str(sid) for sid in skill_result.scalars().all()]

            lessons_json.append(
                {
                    "id": str(lesson.id),
                    "title": lesson.title,
                    "description": lesson.description,
                    "duration_minutes": lesson.duration_minutes,
                    "video_url": lesson.video_url,
                    "order_index": lesson.order_index,
                    "skill_ids": skill_ids,
                }
            )

        weeks_json.append(
            {
                "id": str(week.id),
                "week": week.week_number,
                "theme": week.theme,
                "objectives": week.objectives,
                "order_index": week.order_index,
                "lessons": lessons_json,
            }
        )

    # Update program
    program_query = select(Program).where(Program.id == program_id)
    program_result = await db.execute(program_query)
    program = program_result.scalar_one_or_none()
    if program:
        program.curriculum_json = {"weeks": weeks_json}
        await db.commit()


async def get_program_id_from_curriculum(
    db: AsyncSession, curriculum_id: uuid.UUID
) -> uuid.UUID:
    """Helper to get program_id from a curriculum."""
    query = select(ProgramCurriculum.program_id).where(
        ProgramCurriculum.id == curriculum_id
    )
    result = await db.execute(query)
    program_id = result.scalar_one_or_none()
    if not program_id:
        raise HTTPException(status_code=404, detail="Curriculum not found")
    return program_id


async def get_program_id_from_week(db: AsyncSession, week_id: uuid.UUID) -> uuid.UUID:
    """Helper to get program_id from a week."""
    query = (
        select(ProgramCurriculum.program_id)
        .join(CurriculumWeek, CurriculumWeek.curriculum_id == ProgramCurriculum.id)
        .where(CurriculumWeek.id == week_id)
    )
    result = await db.execute(query)
    program_id = result.scalar_one_or_none()
    if not program_id:
        raise HTTPException(status_code=404, detail="Week not found")
    return program_id


async def get_program_id_from_lesson(
    db: AsyncSession, lesson_id: uuid.UUID
) -> uuid.UUID:
    """Helper to get program_id from a lesson."""
    query = (
        select(ProgramCurriculum.program_id)
        .join(CurriculumWeek, CurriculumWeek.curriculum_id == ProgramCurriculum.id)
        .join(CurriculumLesson, CurriculumLesson.week_id == CurriculumWeek.id)
        .where(CurriculumLesson.id == lesson_id)
    )
    result = await db.execute(query)
    program_id = result.scalar_one_or_none()
    if not program_id:
        raise HTTPException(status_code=404, detail="Lesson not found")
    return program_id
