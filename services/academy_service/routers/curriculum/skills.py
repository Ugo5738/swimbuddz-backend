"""Skills library CRUD — global, reusable skill tags."""

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



router = APIRouter()


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
