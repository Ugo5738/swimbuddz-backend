"""Onboarding schemas (response payload for a new enrollment)."""

from datetime import datetime
from typing import Any, Dict, Optional
from uuid import UUID

from pydantic import BaseModel


class NextSessionInfo(BaseModel):
    """Information about the next scheduled session."""

    date: Optional[datetime] = None
    location: Optional[str] = None
    notes: Optional[str] = None


class OnboardingResponse(BaseModel):
    """Structured onboarding information for a new enrollment."""

    enrollment_id: UUID
    program_name: str
    cohort_name: str
    start_date: datetime
    end_date: datetime
    location: Optional[str] = None
    next_session: Optional[NextSessionInfo] = None
    prep_materials: Optional[Dict[str, Any]] = None
    dashboard_link: str
    resources_link: str
    sessions_link: str
    coach_name: Optional[str] = None
    total_milestones: int
