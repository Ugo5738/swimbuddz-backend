from typing import List
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from services.sessions_service.models import Session
from services.sessions_service.schemas import SessionResponse, SessionCreate, SessionUpdate

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.get("/", response_model=List[SessionResponse])
async def list_sessions(
    db: AsyncSession = Depends(get_async_db),
    # Optional: Add filtering/pagination
):
    """
    List all upcoming sessions.
    """
    # For MVP, just return all, maybe sort by start_time
    query = select(Session).order_by(Session.start_time.asc())
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/stats")
async def get_session_stats(
    db: AsyncSession = Depends(get_async_db),
):
    """
    Get session statistics.
    """
    now = datetime.utcnow()
    query = select(func.count(Session.id)).where(Session.start_time > now)
    result = await db.execute(query)
    upcoming_sessions_count = result.scalar_one() or 0

    return {
        "upcoming_sessions_count": upcoming_sessions_count
    }


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
    session = Session(**session_in.model_dump())
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
