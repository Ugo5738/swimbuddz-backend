"""Program-level curriculum get + create."""

"""
Curriculum and Skills CRUD endpoints.

This router handles:
- Skills library (global, reusable skills)
- Program curriculum (weeks, lessons, skill tagging)
- Sync to Program.curriculum_json after mutations
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
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
)
from services.academy_service.schemas.curriculum import (
    CurriculumLessonResponse,
    CurriculumWeekResponse,
    ProgramCurriculumResponse,
    SkillResponse,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ._helpers import sync_curriculum_json


router = APIRouter()


@router.get(
    "/programs/{program_id}/curriculum", response_model=ProgramCurriculumResponse
)
async def get_program_curriculum(
    program_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    """Get the active curriculum for a program with all weeks, lessons, and skills."""
    query = (
        select(ProgramCurriculum)
        .where(ProgramCurriculum.program_id == program_id)
        .where(ProgramCurriculum.is_active.is_(True))
        .options(
            selectinload(ProgramCurriculum.weeks)
            .selectinload(CurriculumWeek.lessons)
            .selectinload(CurriculumLesson.skills)
            .selectinload(LessonSkill.skill)
        )
    )
    result = await db.execute(query)
    curriculum = result.scalar_one_or_none()

    if not curriculum:
        raise HTTPException(status_code=404, detail="No active curriculum found")

    # Transform to include skills properly
    response = ProgramCurriculumResponse(
        id=curriculum.id,
        program_id=curriculum.program_id,
        version=curriculum.version,
        is_active=curriculum.is_active,
        created_at=curriculum.created_at,
        updated_at=curriculum.updated_at,
        weeks=[],
    )

    # Resolve lesson video URLs in one batch
    lesson_media_ids = [
        lesson.video_media_id
        for week in curriculum.weeks
        for lesson in week.lessons
        if getattr(lesson, "video_media_id", None)
    ]
    media_url_map = (
        await resolve_media_urls(lesson_media_ids) if lesson_media_ids else {}
    )

    for week in sorted(curriculum.weeks, key=lambda w: w.order_index):
        week_response = CurriculumWeekResponse(
            id=week.id,
            curriculum_id=week.curriculum_id,
            week_number=week.week_number,
            theme=week.theme,
            objectives=week.objectives,
            order_index=week.order_index,
            created_at=week.created_at,
            lessons=[],
        )

        for lesson in sorted(week.lessons, key=lambda les: les.order_index):
            # Get skills for this lesson
            skills = [
                SkillResponse(
                    id=ls.skill.id,
                    name=ls.skill.name,
                    category=ls.skill.category,
                    description=ls.skill.description,
                    created_at=ls.skill.created_at,
                )
                for ls in lesson.skills
                if ls.skill
            ]

            video_url = None
            if getattr(lesson, "video_media_id", None):
                video_url = media_url_map.get(lesson.video_media_id)

            lesson_response = CurriculumLessonResponse(
                id=lesson.id,
                week_id=lesson.week_id,
                title=lesson.title,
                description=lesson.description,
                duration_minutes=lesson.duration_minutes,
                video_url=video_url,
                order_index=lesson.order_index,
                created_at=lesson.created_at,
                skills=skills,
            )
            week_response.lessons.append(lesson_response)

        response.weeks.append(week_response)

    return response


@router.post(
    "/programs/{program_id}/curriculum", response_model=ProgramCurriculumResponse
)
async def create_program_curriculum(
    program_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Create a new curriculum version for a program. Deactivates previous versions."""
    # Check program exists
    program_query = select(Program).where(Program.id == program_id)
    program_result = await db.execute(program_query)
    if not program_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Program not found")

    # Deactivate existing curricula
    existing_query = select(ProgramCurriculum).where(
        ProgramCurriculum.program_id == program_id
    )
    existing_result = await db.execute(existing_query)
    existing = existing_result.scalars().all()

    max_version = 0
    for curr in existing:
        curr.is_active = False
        if curr.version > max_version:
            max_version = curr.version

    # Create new curriculum
    curriculum = ProgramCurriculum(
        program_id=program_id,
        version=max_version + 1,
        is_active=True,
    )
    db.add(curriculum)
    await db.commit()
    await db.refresh(curriculum)

    # Sync JSON (empty initially)
    await sync_curriculum_json(db, program_id)

    return ProgramCurriculumResponse(
        id=curriculum.id,
        program_id=curriculum.program_id,
        version=curriculum.version,
        is_active=curriculum.is_active,
        created_at=curriculum.created_at,
        updated_at=curriculum.updated_at,
        weeks=[],
    )
