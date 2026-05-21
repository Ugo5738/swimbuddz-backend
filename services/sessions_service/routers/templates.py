import logging
import uuid
from datetime import datetime, timedelta
from typing import List

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.common.service_client import materialise_opportunities_from_session_template
from libs.db.session import get_async_db
from services.sessions_service.models import Session, SessionTemplate
from services.sessions_service.schemas.templates import (
    GenerateSessionsRequest,
    SessionTemplateCreate,
    SessionTemplateResponse,
    SessionTemplateUpdate,
)

logger = logging.getLogger(__name__)

# Map location slugs to display names
LOCATION_DISPLAY_NAMES: dict[str, str] = {
    "sunfit_pool": "Sunfit Pool",
    "rowe_park_pool": "Rowe Park, Yaba",
    "federal_palace_pool": "Federal Palace Pool",
    "open_water": "Open Water",
}

router = APIRouter(prefix="/sessions/templates", tags=["session-templates"])


@router.get("", response_model=List[SessionTemplateResponse])
async def list_templates(
    active_only: bool = True,
    db: AsyncSession = Depends(get_async_db),
    _admin: AuthUser = Depends(require_admin),
):
    """List all session templates."""
    query = select(SessionTemplate)
    if active_only:
        query = query.where(SessionTemplate.is_active.is_(True))
    query = query.order_by(SessionTemplate.day_of_week, SessionTemplate.start_time)
    result = await db.execute(query)
    templates = result.scalars().all()
    return [SessionTemplateResponse.model_validate(t) for t in templates]


@router.get("/{template_id}", response_model=SessionTemplateResponse)
async def get_template(
    template_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
    _admin: AuthUser = Depends(require_admin),
):
    """Get a specific template."""
    query = select(SessionTemplate).where(SessionTemplate.id == template_id)
    result = await db.execute(query)
    template = result.scalar_one_or_none()

    if not template:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Template not found"
        )
    return SessionTemplateResponse.model_validate(template)


@router.post(
    "", response_model=SessionTemplateResponse, status_code=status.HTTP_201_CREATED
)
async def create_template(
    template_in: SessionTemplateCreate,
    db: AsyncSession = Depends(get_async_db),
    _admin: AuthUser = Depends(require_admin),
):
    """Create a new session template."""
    template_data = template_in.model_dump()
    # Convert naira fee inputs (float) to kobo (int) for DB storage.
    template_data["pool_fee"] = round((template_data.get("pool_fee") or 0.0) * 100)
    template_data["ride_share_fee"] = round(
        (template_data.get("ride_share_fee") or 0.0) * 100
    )
    template = SessionTemplate(**template_data)
    db.add(template)
    await db.commit()
    await db.refresh(template)
    return SessionTemplateResponse.model_validate(template)


@router.patch("/{template_id}", response_model=SessionTemplateResponse)
async def update_template(
    template_id: uuid.UUID,
    template_in: SessionTemplateUpdate,
    db: AsyncSession = Depends(get_async_db),
    _admin: AuthUser = Depends(require_admin),
):
    """Update a session template."""
    query = select(SessionTemplate).where(SessionTemplate.id == template_id)
    result = await db.execute(query)
    template = result.scalar_one_or_none()

    if not template:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Template not found"
        )

    update_data = template_in.model_dump(exclude_unset=True)
    # Convert naira fee inputs (float) to kobo (int) for DB storage.
    if "pool_fee" in update_data and update_data["pool_fee"] is not None:
        update_data["pool_fee"] = round(update_data["pool_fee"] * 100)
    if "ride_share_fee" in update_data and update_data["ride_share_fee"] is not None:
        update_data["ride_share_fee"] = round(update_data["ride_share_fee"] * 100)

    for field, value in update_data.items():
        setattr(template, field, value)

    db.add(template)
    await db.commit()
    await db.refresh(template)
    return SessionTemplateResponse.model_validate(template)


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template(
    template_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
    _admin: AuthUser = Depends(require_admin),
):
    """Delete a session template."""
    query = select(SessionTemplate).where(SessionTemplate.id == template_id)
    result = await db.execute(query)
    template = result.scalar_one_or_none()

    if not template:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Template not found"
        )

    await db.delete(template)
    await db.commit()


@router.post("/{template_id}/generate")
async def generate_sessions(
    template_id: uuid.UUID,
    request: GenerateSessionsRequest,
    db: AsyncSession = Depends(get_async_db),
    _admin: AuthUser = Depends(require_admin),
):
    """Generate sessions from a template for the specified number of weeks."""
    # Get the template
    query = select(SessionTemplate).where(SessionTemplate.id == template_id)
    result = await db.execute(query)
    template = result.scalar_one_or_none()

    if not template:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Template not found"
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
                    Session.starts_at <= end_datetime,
                    Session.ends_at >= start_datetime,
                )
            )
            conflict_result = await db.execute(conflict_query)
            if conflict_result.scalars().first():
                conflicts.append(
                    {
                        "date": session_date.isoformat(),
                        "reason": "Session already exists at this time",
                    }
                )
                continue

        # Create the session
        # New templates carry pool_id + location_name; legacy templates only
        # have the `location` enum string, which we fall back to via the
        # display-name map.
        if template.pool_id:
            session_location_name = template.location_name
        else:
            session_location_name = LOCATION_DISPLAY_NAMES.get(
                template.location, template.location
            )
        session = Session(
            title=template.title,
            description=template.description,
            pool_id=template.pool_id,
            location_name=session_location_name,
            session_type=template.session_type,
            pool_fee=template.pool_fee,  # both are kobo integers after migration
            capacity=template.capacity,
            starts_at=start_datetime,
            ends_at=end_datetime,
            template_id=template.id,
            is_recurring_instance=True,
        )
        db.add(session)
        await db.flush()  # Flush to get session ID

        # If template has ride share config, attach it to the session
        if template.ride_share_config:
            try:
                from libs.common.config import get_settings

                settings = get_settings()
                async with httpx.AsyncClient() as client:
                    await client.post(
                        f"{settings.TRANSPORT_SERVICE_URL}/transport/sessions/{session.id}/ride-configs",
                        json=template.ride_share_config,
                        timeout=5.0,
                    )
            except Exception as e:
                # Log error but don't fail session creation
                print(
                    f"Warning: Failed to attach ride config to session {session.id}: {e}"
                )

        created_sessions.append(
            {
                "date": session_date.isoformat(),
                "start_time": start_datetime.isoformat(),
                "end_time": end_datetime.isoformat(),
                # Carried only inside this function — stripped before we
                # build the response, so the API contract is unchanged.
                "_session_id": str(session.id),
                "_local_date": session_date.isoformat(),
                "_local_start_time": start_datetime.time().isoformat(),
                "_local_end_time": end_datetime.time().isoformat(),
            }
        )

    await db.commit()

    # Fan out volunteer opportunities for each session, based on the parent
    # template's SessionTemplateVolunteerSlot rows. Best effort: a
    # volunteer-service outage must not roll back the sessions we just
    # committed. See docs/design/VOLUNTEER_OPPORTUNITY_CONTEXT_DESIGN.md.
    for entry in created_sessions:
        try:
            await materialise_opportunities_from_session_template(
                calling_service="sessions",
                session_id=entry["_session_id"],
                session_template_id=str(template.id),
                date=entry["_local_date"],
                start_time=entry["_local_start_time"],
                end_time=entry["_local_end_time"],
                location_name=session_location_name,
            )
        except Exception as exc:
            logger.error(
                "Failed to materialise volunteer opportunities for session %s (template %s): %s",
                entry["_session_id"],
                template.id,
                exc,
            )

    # Strip internal fields from the API response.
    response_sessions = [
        {k: v for k, v in entry.items() if not k.startswith("_")}
        for entry in created_sessions
    ]

    return {
        "created": len(response_sessions),
        "skipped": len(conflicts),
        "sessions": response_sessions,
        "conflicts": conflicts,
    }
