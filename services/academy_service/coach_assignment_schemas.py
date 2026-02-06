"""Pydantic schemas for coach assignment endpoints."""

import uuid
from datetime import date, datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

# ============================================================================
# ENUMS
# ============================================================================


class AssignmentRoleEnum(str, Enum):
    LEAD = "lead"
    ASSISTANT = "assistant"
    SHADOW = "shadow"
    OBSERVER = "observer"


class AssignmentStatusEnum(str, Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class EvalRecommendation(str, Enum):
    CONTINUE_SHADOW = "continue_shadow"
    READY_FOR_ASSISTANT = "ready_for_assistant"
    READY_FOR_LEAD = "ready_for_lead"


# ============================================================================
# COACH ASSIGNMENT SCHEMAS
# ============================================================================


class CoachAssignmentCreate(BaseModel):
    """Create a new coach assignment."""

    cohort_id: uuid.UUID
    coach_id: uuid.UUID
    role: AssignmentRoleEnum
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    notes: Optional[str] = None
    is_session_override: bool = False
    session_date: Optional[date] = None


class CoachAssignmentUpdate(BaseModel):
    """Update an existing coach assignment."""

    role: Optional[AssignmentRoleEnum] = None
    status: Optional[AssignmentStatusEnum] = None
    end_date: Optional[datetime] = None
    notes: Optional[str] = None


class CoachAssignmentResponse(BaseModel):
    """Response for a coach assignment."""

    id: str
    cohort_id: str
    coach_id: str
    role: str
    start_date: datetime
    end_date: Optional[datetime] = None
    assigned_by_id: str
    status: str
    notes: Optional[str] = None
    is_session_override: bool = False
    session_date: Optional[date] = None
    created_at: datetime
    updated_at: datetime

    # Populated in responses
    coach_name: Optional[str] = None
    cohort_name: Optional[str] = None
    program_name: Optional[str] = None


# ============================================================================
# SHADOW EVALUATION SCHEMAS
# ============================================================================


class ShadowEvaluationCreate(BaseModel):
    """Create a shadow evaluation for a coach assignment."""

    session_date: date
    scores: dict = Field(
        ...,
        description="JSON scores e.g. {'communication': 4, 'safety': 5, 'technique_demo': 3}",
    )
    feedback: Optional[str] = None
    recommendation: EvalRecommendation


class ShadowEvaluationResponse(BaseModel):
    """Response for a shadow evaluation."""

    id: str
    assignment_id: str
    evaluator_id: str
    session_date: date
    scores: dict
    feedback: Optional[str] = None
    recommendation: str
    created_at: datetime

    evaluator_name: Optional[str] = None


# ============================================================================
# READINESS SCHEMAS
# ============================================================================


class ReadinessCheckStatus(str, Enum):
    PASSED = "passed"
    PENDING = "pending"
    FAILED = "failed"


class ReadinessCheckItem(BaseModel):
    """Individual readiness check result."""

    name: str
    description: str
    status: ReadinessCheckStatus
    required: bool = True
    details: Optional[str] = None


class CoachReadinessResponse(BaseModel):
    """Coach readiness assessment for a target grade."""

    coach_id: str
    coach_name: Optional[str] = None
    target_grade: str
    is_ready: bool
    checks: list[ReadinessCheckItem]
    missing_requirements: list[str]
    recommendations: list[str]
