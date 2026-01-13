import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from libs.auth.dependencies import require_admin, require_coach
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from services.sessions_service.models import Session, SessionCoach
from services.sessions_service.schemas import (
    SessionCreate,
    SessionResponse,
    SessionUpdate,
)
from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.get("/", response_model=List[SessionResponse])
async def list_sessions(
    types: Optional[str] = None,
    cohort_id: Optional[uuid.UUID] = None,
    db: AsyncSession = Depends(get_async_db),
):
    """
    List all upcoming sessions. Optional `types` filter is a comma-separated list
    of SessionType values (e.g., "club,community"). Optional `cohort_id` filter
    returns only sessions for that cohort.
    """
    query = select(Session).order_by(Session.starts_at.asc())

    if types:
        type_values = [t.strip() for t in types.split(",") if t.strip()]
        if type_values:
            query = query.where(Session.session_type.in_(type_values))

    if cohort_id:
        query = query.where(Session.cohort_id == cohort_id)

    result = await db.execute(query)
    return result.scalars().all()


@router.get("/stats")
async def get_session_stats(
    db: AsyncSession = Depends(get_async_db),
):
    """
    Get session statistics.
    """
    now = datetime.now(timezone.utc)
    query = select(func.count(Session.id)).where(Session.starts_at > now)
    result = await db.execute(query)
    upcoming_sessions_count = result.scalar_one() or 0

    return {"upcoming_sessions_count": upcoming_sessions_count}


@router.get("/coach/me", response_model=List[SessionResponse])
async def list_my_coach_sessions(
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    current_user: AuthUser = Depends(require_coach),
    db: AsyncSession = Depends(get_async_db),
):
    """
    List sessions for the current coach. Includes:
    - Sessions linked to cohorts where the coach is assigned
    - Sessions where the coach is listed in session_coaches

    Optional date range filters (ISO format: YYYY-MM-DD).
    """
    # 1. Resolve Member ID (lookup by auth_id)
    member_row = await db.execute(
        text("SELECT id FROM members WHERE auth_id = :auth_id"),
        {"auth_id": current_user.user_id},
    )
    member = member_row.mappings().first()

    if not member:
        raise HTTPException(status_code=404, detail="Member profile not found")

    member_id = member["id"]

    # 2. Get cohort IDs where this coach is assigned
    cohort_query = await db.execute(
        text("SELECT id FROM cohorts WHERE coach_id = :coach_id"),
        {"coach_id": member_id},
    )
    cohort_ids = [row[0] for row in cohort_query.fetchall()]

    # 3. Get session IDs where coach is directly assigned
    session_coach_query = select(SessionCoach.session_id).where(
        SessionCoach.coach_id == member_id
    )
    session_coach_result = await db.execute(session_coach_query)
    direct_session_ids = [row[0] for row in session_coach_result.fetchall()]

    # 4. Build combined query
    conditions = []
    if cohort_ids:
        conditions.append(Session.cohort_id.in_(cohort_ids))
    if direct_session_ids:
        conditions.append(Session.id.in_(direct_session_ids))

    if not conditions:
        return []

    query = select(Session).where(or_(*conditions))

    # 5. Apply date filters
    if from_date:
        try:
            from_dt = datetime.fromisoformat(from_date)
            query = query.where(Session.starts_at >= from_dt)
        except ValueError:
            pass

    if to_date:
        try:
            to_dt = datetime.fromisoformat(to_date)
            query = query.where(Session.starts_at <= to_dt)
        except ValueError:
            pass

    query = query.order_by(Session.starts_at.asc())
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/{session_id}", response_model=SessionResponse)
async def get_session(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    """
    Get details of a specific session.
    """
    query = select(Session).where(Session.id == session_id)
    result = await db.execute(query)
    session = result.scalar_one_or_none()

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )
    return session


@router.post("/", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
async def create_session(
    session_in: SessionCreate,
    current_user: AuthUser = Depends(require_admin),  # Only admins can create sessions
    db: AsyncSession = Depends(get_async_db),
):
    """
    Create a new session (Admin only).
    """
    # Validate cohort_id exists (stub query to academy_service's cohorts table)
    if session_in.cohort_id:
        cohort_check = await db.execute(
            text("SELECT id FROM cohorts WHERE id = :cohort_id"),
            {"cohort_id": session_in.cohort_id},
        )
        if not cohort_check.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid cohort_id: cohort does not exist",
            )

    session_data = session_in.model_dump()
    # Remove ride_share_areas if present in input, though schema should handle it
    session_data.pop("ride_share_areas", None)

    session = Session(**session_data)
    db.add(session)
    await db.commit()
    await db.refresh(session)

    return session


@router.patch("/{session_id}", response_model=SessionResponse)
async def update_session(
    session_id: uuid.UUID,
    session_in: SessionUpdate,
    db: AsyncSession = Depends(get_async_db),
):
    """
    Update a session.
    """
    query = select(Session).where(Session.id == session_id)
    result = await db.execute(query)
    session = result.scalar_one_or_none()

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    update_data = session_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(session, field, value)

    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    """
    Delete a session.
    """
    query = select(Session).where(Session.id == session_id)
    result = await db.execute(query)
    session = result.scalar_one_or_none()

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    await db.delete(session)
    await db.commit()
