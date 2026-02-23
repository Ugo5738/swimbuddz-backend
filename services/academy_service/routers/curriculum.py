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

router = APIRouter(tags=["curriculum"])


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================


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


# ============================================================================
# SKILLS LIBRARY ENDPOINTS
# ============================================================================


@router.get("/skills", response_model=List[SkillResponse])
async def list_skills(
    category: Optional[str] = None,
    db: AsyncSession = Depends(get_async_db),
):
    """List all skills, optionally filtered by category."""
    query = select(Skill).order_by(Skill.category, Skill.name)
    if category:
        query = query.where(Skill.category == category)
    result = await db.execute(query)
    return result.scalars().all()


@router.post("/skills", response_model=SkillResponse)
async def create_skill(
    skill_in: SkillCreate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Create a new skill in the global library."""
    skill = Skill(**skill_in.model_dump())
    db.add(skill)
    await db.commit()
    await db.refresh(skill)
    return skill


@router.put("/skills/{skill_id}", response_model=SkillResponse)
async def update_skill(
    skill_id: uuid.UUID,
    skill_in: SkillUpdate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Update an existing skill."""
    query = select(Skill).where(Skill.id == skill_id)
    result = await db.execute(query)
    skill = result.scalar_one_or_none()

    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")

    update_data = skill_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(skill, field, value)

    await db.commit()
    await db.refresh(skill)
    return skill


@router.delete("/skills/{skill_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_skill(
    skill_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Delete a skill from the library."""
    query = select(Skill).where(Skill.id == skill_id)
    result = await db.execute(query)
    skill = result.scalar_one_or_none()

    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")

    # Delete associated lesson_skills first
    await db.execute(delete(LessonSkill).where(LessonSkill.skill_id == skill_id))
    await db.delete(skill)
    await db.commit()
    return None


# ============================================================================
# PROGRAM CURRICULUM ENDPOINTS
# ============================================================================


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


# ============================================================================
# CURRICULUM WEEK ENDPOINTS
# ============================================================================


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


# ============================================================================
# CURRICULUM LESSON ENDPOINTS
# ============================================================================


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


# ============================================================================
# REORDER ENDPOINTS
# ============================================================================


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
