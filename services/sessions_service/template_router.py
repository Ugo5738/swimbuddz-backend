from typing import List
import uuid
from datetime import datetime, timedelta, time as time_type

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from services.sessions_service.session_template import SessionTemplate
from services.sessions_service.template_schemas import (
    SessionTemplateResponse, SessionTemplateCreate, SessionTemplateUpdate, GenerateSessionsRequest
)
from services.sessions_service.models import Session, SessionLocation

router = APIRouter(prefix="/sessions/templates", tags=["session-templates"])


@router.get("", response_model=List[SessionTemplateResponse])
async def list_templates(
    active_only: bool = True,
    db: AsyncSession = Depends(get_async_db),
):
    """List all session templates."""
    query = select(SessionTemplate)
    if active_only:
        query = query.where(SessionTemplate.is_active == True)
    query = query.order_by(SessionTemplate.day_of_week, SessionTemplate.start_time)
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/{template_id}", response_model=SessionTemplateResponse)
async def get_template(
    template_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    """Get a specific template."""
    query = select(SessionTemplate).where(SessionTemplate.id == template_id)
    result = await db.execute(query)
    template = result.scalar_one_or_none()
    
    if not template:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Template not found"
        )
    return template


@router.post("", response_model=SessionTemplateResponse, status_code=status.HTTP_201_CREATED)
async def create_template(
    template_in: SessionTemplateCreate,
    db: AsyncSession = Depends(get_async_db),
):
    """Create a new session template."""
    template = SessionTemplate(**template_in.model_dump())
    db.add(template)
    await db.commit()
    await db.refresh(template)
    return template


@router.patch("/{template_id}", response_model=SessionTemplateResponse)
async def update_template(
    template_id: uuid.UUID,
    template_in: SessionTemplateUpdate,
    db: AsyncSession = Depends(get_async_db),
):
    """Update a session template."""
    query = select(SessionTemplate).where(SessionTemplate.id == template_id)
    result = await db.execute(query)
    template = result.scalar_one_or_none()
    
    if not template:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Template not found"
        )
    
    update_data = template_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(template, field, value)
    
    db.add(template)
    await db.commit()
    await db.refresh(template)
    return template


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template(
    template_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    """Delete a session template."""
    query = select(SessionTemplate).where(SessionTemplate.id == template_id)
    result = await db.execute(query)
    template = result.scalar_one_or_none()
    
    if not template:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Template not found"
        )
    
    await db.delete(template)
    await db.commit()


@router.post("/{template_id}/generate")
async def generate_sessions(
    template_id: uuid.UUID,
    request: GenerateSessionsRequest,
    db: AsyncSession = Depends(get_async_db),
):
    """Generate sessions from a template for the specified number of weeks."""
    # Get the template
    query = select(SessionTemplate).where(SessionTemplate.id == template_id)
    result = await db.execute(query)
    template = result.scalar_one_or_none()
    
    if not template:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Template not found"
        )
    
    # Find the next occurrence of the template's day of week
    today = datetime.now().date()
    days_ahead = (template.day_of_week - today.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7  # Start from next week
    
    created_sessions = []
    conflicts = []
    
    for week in range(request.weeks):
        session_date = today + timedelta(days=days_ahead + (week * 7))
        
        # Combine date with template time and localize to configured timezone
        from zoneinfo import ZoneInfo
        from libs.common.config import get_settings
        settings = get_settings()
        
        local_tz = ZoneInfo(settings.TIMEZONE)
        # Create naive datetime first
        naive_dt = datetime.combine(session_date, template.start_time)
        # Localize it (this tells Python "this time is in Europe/London")
        local_dt = naive_dt.replace(tzinfo=local_tz)
        # Convert to UTC for storage
        start_datetime = local_dt.astimezone(ZoneInfo("UTC"))
        
        end_datetime = start_datetime + timedelta(minutes=template.duration_minutes)
        
        # Check for conflicts if skip_conflicts is True
        if request.skip_conflicts:
            conflict_query = select(Session).where(
                and_(
                    Session.start_time <= end_datetime,
                    Session.end_time >= start_datetime
                )
            )
            conflict_result = await db.execute(conflict_query)
            if conflict_result.scalar_one_or_none():
                conflicts.append({
                    "date": session_date.isoformat(),
                    "reason": "Session already exists at this time"
                })
                continue
        
        # Create the session
        session = Session(
            title=template.title,
            description=template.description,
            location=SessionLocation(template.location),
            pool_fee=template.pool_fee,
            capacity=template.capacity,
            start_time=start_datetime,
            end_time=end_datetime,
            template_id=template.id,
            is_recurring_instance=True
        )
        db.add(session)
        created_sessions.append({
            "date": session_date.isoformat(),
            "start_time": start_datetime.isoformat(),
            "end_time": end_datetime.isoformat()
        })
    
    await db.commit()
    
    return {
        "created": len(created_sessions),
        "skipped": len(conflicts),
        "sessions": created_sessions,
        "conflicts": conflicts
    }
