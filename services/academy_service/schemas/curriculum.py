"""
Pydantic schemas for Curriculum and Skills CRUD operations.
"""

from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict

# ============================================================================
# SKILL SCHEMAS
# ============================================================================


class SkillCreate(BaseModel):
    """Create a new skill in the global skills library."""

    name: str
    category: str  # "water_confidence", "stroke", "safety", "technique"
    description: Optional[str] = None


class SkillUpdate(BaseModel):
    """Update an existing skill."""

    name: Optional[str] = None
    category: Optional[str] = None
    description: Optional[str] = None


class SkillResponse(BaseModel):
    """Skill response with all fields."""

    id: UUID
    name: str
    category: str
    description: Optional[str]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ============================================================================
# CURRICULUM LESSON SCHEMAS
# ============================================================================


class CurriculumLessonCreate(BaseModel):
    """Create a lesson within a week."""

    title: str
    description: Optional[str] = None
    duration_minutes: Optional[int] = None
    video_url: Optional[str] = None
    skill_ids: Optional[List[UUID]] = None  # Skills to tag on this lesson


class CurriculumLessonUpdate(BaseModel):
    """Update a lesson."""

    title: Optional[str] = None
    description: Optional[str] = None
    duration_minutes: Optional[int] = None
    video_url: Optional[str] = None
    order_index: Optional[int] = None
    skill_ids: Optional[List[UUID]] = None  # Update skill tags


class CurriculumLessonResponse(BaseModel):
    """Lesson response with skills."""

    id: UUID
    week_id: UUID
    title: str
    description: Optional[str]
    duration_minutes: Optional[int]
    video_url: Optional[str]
    order_index: int
    skills: List[SkillResponse] = []
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ============================================================================
# CURRICULUM WEEK SCHEMAS
# ============================================================================


class CurriculumWeekCreate(BaseModel):
    """Create a week within a curriculum."""

    week_number: int
    theme: str
    objectives: Optional[str] = None


class CurriculumWeekUpdate(BaseModel):
    """Update a week."""

    week_number: Optional[int] = None
    theme: Optional[str] = None
    objectives: Optional[str] = None
    order_index: Optional[int] = None


class CurriculumWeekResponse(BaseModel):
    """Week response with lessons."""

    id: UUID
    curriculum_id: UUID
    week_number: int
    theme: str
    objectives: Optional[str]
    order_index: int
    lessons: List[CurriculumLessonResponse] = []
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ============================================================================
# PROGRAM CURRICULUM SCHEMAS
# ============================================================================


class ProgramCurriculumCreate(BaseModel):
    """Create a new curriculum version for a program."""

    pass  # Version number is auto-assigned


class ProgramCurriculumResponse(BaseModel):
    """Full curriculum response with weeks and lessons."""

    id: UUID
    program_id: UUID
    version: int
    is_active: bool
    weeks: List[CurriculumWeekResponse] = []
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# Resolve forward references
CurriculumLessonResponse.model_rebuild()
CurriculumWeekResponse.model_rebuild()
ProgramCurriculumResponse.model_rebuild()
