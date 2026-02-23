"""Internal service-to-service endpoints for sessions-service.

These endpoints are authenticated with service_role JWT only.
They are NOT exposed through the gateway — only other backend services
call them directly via Docker network.
"""

import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from libs.auth.dependencies import require_service_role
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from pydantic import BaseModel
from services.sessions_service.models import Session, SessionCoach, SessionStatus
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/internal", tags=["internal"])


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class SessionBasic(BaseModel):
    id: str
    title: str
    session_type: str
    status: str
    starts_at: str
    ends_at: str
    location_name: Optional[str] = None
    location_address: Optional[str] = None
    location: Optional[str] = None
    cohort_id: Optional[str] = None
    capacity: int
    pool_fee: Optional[float] = None
    week_number: Optional[int] = None
    lesson_title: Optional[str] = None


class NextSessionResponse(BaseModel):
    starts_at: str
    title: str
    location_name: Optional[str] = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

# NOTE: Static path "/sessions/scheduled" must be registered before the
# parameterized "/sessions/{session_id}" to avoid route collision (FastAPI
# matches routes in definition order).


@router.get("/sessions/scheduled", response_model=List[SessionBasic])
async def get_scheduled_sessions(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Get scheduled sessions within a date range."""
    query = select(Session).where(Session.status == SessionStatus.SCHEDULED)
    if start_date:
        query = query.where(Session.starts_at >= start_date)
    if end_date:
        query = query.where(Session.starts_at < end_date)
    query = query.order_by(Session.starts_at.asc())
    result = await db.execute(query)
    sessions = result.scalars().all()
    return [
        SessionBasic(
            id=str(s.id),
            title=s.title,
            session_type=s.session_type.value,
            status=s.status.value,
            starts_at=s.starts_at.isoformat(),
            ends_at=s.ends_at.isoformat(),
            location_name=s.location_name,
            location_address=s.location_address,
            location=s.location.value if s.location else None,
            cohort_id=str(s.cohort_id) if s.cohort_id else None,
            capacity=s.capacity,
            pool_fee=s.pool_fee,
            week_number=s.week_number,
            lesson_title=s.lesson_title,
        )
        for s in sessions
    ]


@router.get("/sessions/{session_id}", response_model=SessionBasic)
async def get_session_by_id(
    session_id: uuid.UUID,
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Look up a session by ID."""
    result = await db.execute(select(Session).where(Session.id == session_id))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return SessionBasic(
        id=str(session.id),
        title=session.title,
        session_type=session.session_type.value,
        status=session.status.value,
        starts_at=session.starts_at.isoformat(),
        ends_at=session.ends_at.isoformat(),
        location_name=session.location_name,
        location_address=session.location_address,
        location=session.location.value if session.location else None,
        cohort_id=str(session.cohort_id) if session.cohort_id else None,
        capacity=session.capacity,
        pool_fee=session.pool_fee,
        week_number=session.week_number,
        lesson_title=session.lesson_title,
    )


@router.get("/cohorts/{cohort_id}/next-session", response_model=NextSessionResponse)
async def get_next_session_for_cohort(
    cohort_id: uuid.UUID,
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Get the next upcoming session for a cohort."""
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(Session)
        .where(
            Session.cohort_id == cohort_id,
            Session.starts_at > now,
            Session.status == SessionStatus.SCHEDULED,
        )
        .order_by(Session.starts_at.asc())
        .limit(1)
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="No upcoming session found")
    return NextSessionResponse(
        starts_at=session.starts_at.isoformat(),
        title=session.title,
        location_name=session.location_name,
    )


@router.get("/cohorts/{cohort_id}/session-ids", response_model=List[str])
async def get_session_ids_for_cohort(
    cohort_id: uuid.UUID,
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Get all session IDs for a cohort."""
    result = await db.execute(
        select(Session.id)
        .where(Session.cohort_id == cohort_id)
        .order_by(Session.starts_at.asc())
    )
    return [str(row[0]) for row in result.all()]


@router.get("/cohorts/{cohort_id}/completed-session-ids", response_model=List[str])
async def get_completed_session_ids_for_cohort(
    cohort_id: uuid.UUID,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Get completed session IDs for a cohort, optionally filtered by date range."""
    query = select(Session.id).where(
        Session.cohort_id == cohort_id,
        Session.status == SessionStatus.COMPLETED,
    )
    if start_date:
        query = query.where(Session.starts_at >= start_date)
    if end_date:
        query = query.where(Session.starts_at <= end_date)
    query = query.order_by(Session.starts_at.asc())
    result = await db.execute(query)
    return [str(row[0]) for row in result.all()]


@router.get("/sessions/{session_id}/coaches", response_model=List[str])
async def get_session_coach_ids(
    session_id: uuid.UUID,
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Get coach member IDs for a session."""
    result = await db.execute(
        select(SessionCoach.coach_id).where(SessionCoach.session_id == session_id)
    )
    return [str(row[0]) for row in result.all()]
