"""Internal service-to-service volunteer endpoints.

Called by other SwimBuddz services (e.g. members_service on registration)
via service-role JWT, not by frontend clients.
"""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends
from libs.auth.dependencies import require_service_role
from libs.auth.models import AuthUser
from libs.common.logging import get_logger
from libs.db.session import get_async_db
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.volunteer_service.models import VolunteerProfile, VolunteerRole

logger = get_logger(__name__)
router = APIRouter(prefix="/internal/volunteer", tags=["internal-volunteer"])


class EnsureProfileRequest(BaseModel):
    member_id: str
    volunteer_interests: Optional[list[str]] = None  # category strings


class EnsureProfileResponse(BaseModel):
    success: bool
    created: bool
    profile_id: str


@router.post("/ensure-profile", response_model=EnsureProfileResponse)
async def ensure_volunteer_profile(
    body: EnsureProfileRequest,
    _service: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Create a VolunteerProfile for a member if one doesn't already exist.

    Idempotent: returns success with created=False if profile exists.
    """
    member_id = uuid.UUID(body.member_id)

    existing = (
        await db.execute(
            select(VolunteerProfile).where(VolunteerProfile.member_id == member_id)
        )
    ).scalar_one_or_none()

    if existing:
        logger.info("Volunteer profile already exists for member %s", body.member_id)
        return EnsureProfileResponse(
            success=True, created=False, profile_id=str(existing.id)
        )

    # Map category strings to role IDs
    preferred_roles: list[str] = []
    if body.volunteer_interests:
        categories = [c.lower() for c in body.volunteer_interests]
        roles = (
            (
                await db.execute(
                    select(VolunteerRole).where(VolunteerRole.is_active.is_(True))
                )
            )
            .scalars()
            .all()
        )
        for role in roles:
            if role.category.value.lower() in categories:
                preferred_roles.append(str(role.id))

    profile = VolunteerProfile(
        member_id=member_id,
        preferred_roles=preferred_roles or None,
    )
    db.add(profile)
    await db.commit()
    await db.refresh(profile)

    logger.info(
        "Created volunteer profile for member %s (roles=%s)",
        body.member_id,
        preferred_roles,
    )
    return EnsureProfileResponse(success=True, created=True, profile_id=str(profile.id))
