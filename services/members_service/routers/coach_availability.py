"""Coach availability editor routes.

A coach publishes their own recurring weekly availability + blackout dates.
This is the typed, validated editor surface — the loose ``availability_calendar``
dict accepted by the onboarding/preferences endpoints is NOT validated.
Downstream, sessions_service reads this to compute bookable make-up slots.

See docs/design/AVAILABILITY_AND_MAKEUP_SCHEDULING_DESIGN.md (Phase 0).
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from libs.auth.dependencies import get_current_user
from libs.auth.models import AuthUser
from libs.common.logging import get_logger
from libs.db.session import get_async_db
from services.members_service.models import CoachProfile, Member
from services.members_service.schemas import (
    CoachAvailabilityCalendar,
    CoachAvailabilityResponse,
    CoachAvailabilityUpdate,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

router = APIRouter(prefix="/coaches", tags=["coaches"])


async def _resolve_coach_profile(
    db: AsyncSession, auth_id: Optional[str]
) -> CoachProfile:
    """Resolve the authenticated user's CoachProfile, or raise 401/404."""
    if not auth_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication"
        )
    result = await db.execute(select(Member).where(Member.auth_id == auth_id))
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Member not found"
        )
    result = await db.execute(
        select(CoachProfile).where(CoachProfile.member_id == member.id)
    )
    coach = result.scalar_one_or_none()
    if not coach:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Coach profile not found"
        )
    return coach


@router.get("/me/availability", response_model=CoachAvailabilityResponse)
async def get_my_availability(
    db: AsyncSession = Depends(get_async_db),
    current_user: AuthUser = Depends(get_current_user),
) -> CoachAvailabilityResponse:
    """Return the current coach's availability calendar + spacing override."""
    coach = await _resolve_coach_profile(db, current_user.user_id)

    availability: Optional[CoachAvailabilityCalendar] = None
    if coach.availability_calendar:
        try:
            availability = CoachAvailabilityCalendar.model_validate(
                coach.availability_calendar
            )
        except Exception:
            # Legacy/loose data set via onboarding — not in the typed shape.
            # Return null until the coach re-saves through this editor.
            logger.warning(
                "Coach %s has availability_calendar that fails typed validation; "
                "returning null.",
                coach.member_id,
            )

    return CoachAvailabilityResponse(
        availability=availability,
        min_hours_between_sessions=coach.min_hours_between_sessions,
    )


@router.put("/me/availability", response_model=CoachAvailabilityResponse)
async def set_my_availability(
    data: CoachAvailabilityUpdate,
    db: AsyncSession = Depends(get_async_db),
    current_user: AuthUser = Depends(get_current_user),
) -> CoachAvailabilityResponse:
    """Replace the current coach's availability calendar (and spacing override).

    The body is fully validated (weekday, 24-hour times, no same-day overlaps,
    known IANA timezone). ``min_hours_between_sessions`` is left unchanged when
    omitted. Stored as JSON on the coach profile.
    """
    coach = await _resolve_coach_profile(db, current_user.user_id)

    coach.availability_calendar = data.availability.model_dump(mode="json")
    if data.min_hours_between_sessions is not None:
        coach.min_hours_between_sessions = data.min_hours_between_sessions

    await db.commit()
    await db.refresh(coach)

    logger.info(
        "Coach %s updated availability: %d recurring block(s), %d blackout(s)",
        coach.member_id,
        len(data.availability.recurring),
        len(data.availability.blackouts),
    )

    return CoachAvailabilityResponse(
        availability=data.availability,
        min_hours_between_sessions=coach.min_hours_between_sessions,
    )
