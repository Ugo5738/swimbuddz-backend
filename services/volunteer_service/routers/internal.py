"""Internal service-to-service volunteer endpoints.

Called by other SwimBuddz services (e.g. members_service on registration)
via service-role JWT, not by frontend clients.
"""

import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_service_role
from libs.auth.models import AuthUser
from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from libs.db.session import get_async_db
from services.volunteer_service.models import VolunteerProfile, VolunteerRole
from services.volunteer_service.models.core import VolunteerHoursLog

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


# ---------------------------------------------------------------------------
# Hours logging (idempotent cross-service grant)
# ---------------------------------------------------------------------------


class LogHoursRequest(BaseModel):
    """Idempotent hours-credit request from another service.

    The (source, external_reference_id, member_id) tuple identifies the
    granting event. If a row with the same tuple already exists, the
    endpoint is a no-op and returns the existing log id — so retries from
    the calling service never double-credit.
    """

    member_id: str = Field(..., description="Members-service Member.id")
    hours: float = Field(..., gt=0)
    source: str = Field(
        ...,
        description="e.g. 'challenge_completion'. Free-form string, kept "
        "consistent across calls so idempotency lookup works.",
    )
    external_reference_id: str = Field(
        ...,
        description="Stable id from the granting event (e.g. challenge "
        "submission id). Used together with source + member_id for "
        "idempotency.",
    )
    logged_by: Optional[str] = Field(
        default=None,
        description="Auth UUID of the admin/service that triggered the grant.",
    )
    notes: Optional[str] = None


class LogHoursResponse(BaseModel):
    success: bool
    created: bool
    log_id: str


@router.post("/log-hours", response_model=LogHoursResponse, status_code=201)
async def internal_log_hours(
    body: LogHoursRequest,
    caller: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Credit volunteer hours to a member, idempotently.

    The (source, external_reference_id, member_id) tuple is enforced
    unique by a partial unique index in the migration that ships with
    this endpoint. If the tuple already exists, this is a no-op and
    returns created=False with the existing log id.

    Auth: service-role JWT only.
    """
    try:
        member_uuid = uuid.UUID(body.member_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid member_id")

    # Idempotency: short-circuit if an identical (source, ext_ref, member) row
    # already exists. The DB-level partial unique index is the safety net for
    # concurrent retries; this lookup is the cheap happy path.
    existing = (
        await db.execute(
            select(VolunteerHoursLog).where(
                VolunteerHoursLog.source == body.source,
                VolunteerHoursLog.external_reference_id == body.external_reference_id,
                VolunteerHoursLog.member_id == member_uuid,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        logger.info(
            "Hours-log idempotency hit: source=%s ref=%s member=%s log_id=%s",
            body.source,
            body.external_reference_id,
            body.member_id,
            existing.id,
        )
        return LogHoursResponse(success=True, created=False, log_id=str(existing.id))

    logged_by_uuid: Optional[uuid.UUID] = None
    if body.logged_by:
        try:
            logged_by_uuid = uuid.UUID(body.logged_by)
        except (ValueError, TypeError):
            logged_by_uuid = None

    log = VolunteerHoursLog(
        member_id=member_uuid,
        hours=body.hours,
        date=utc_now().date(),
        source=body.source,
        external_reference_id=body.external_reference_id,
        logged_by=logged_by_uuid,
        notes=body.notes,
    )
    db.add(log)

    # Keep the volunteer profile's denormalised total in sync if a profile
    # exists. Missing profile is OK — the immutable hours log is the
    # source of truth.
    profile = (
        await db.execute(
            select(VolunteerProfile).where(VolunteerProfile.member_id == member_uuid)
        )
    ).scalar_one_or_none()
    if profile is not None:
        profile.total_hours = (profile.total_hours or 0.0) + body.hours

    await db.commit()
    await db.refresh(log)

    logger.info(
        "Logged %.2f hours for member %s from %s (ref=%s)",
        body.hours,
        body.member_id,
        body.source,
        body.external_reference_id,
    )
    return LogHoursResponse(success=True, created=True, log_id=str(log.id))


# ---------------------------------------------------------------------------
# Reporting: member volunteer summary
# ---------------------------------------------------------------------------


class MemberVolunteerSummary(BaseModel):
    total_hours: float = 0.0


@router.get(
    "/member-summary/{member_auth_id}",
    response_model=MemberVolunteerSummary,
)
async def get_member_volunteer_summary(
    member_auth_id: str,
    date_from: datetime = Query(..., alias="from"),
    date_to: datetime = Query(..., alias="to"),
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Aggregate volunteer hours for a member within a date range.

    Used by the reporting service for quarterly reports.
    Looks up member_id from auth_id via raw SQL on the members table,
    then sums hours from VolunteerHoursLog.
    """
    from sqlalchemy import text

    from services.volunteer_service.models.core import VolunteerHoursLog

    # Look up member_id from auth_id via the shared members table
    member_result = await db.execute(
        text("SELECT id FROM members WHERE auth_id = :auth_id"),
        {"auth_id": member_auth_id},
    )
    row = member_result.first()
    if row is None:
        return MemberVolunteerSummary()

    member_uuid = row[0]

    result = await db.execute(
        select(
            func.coalesce(func.sum(VolunteerHoursLog.hours), 0.0).label("total")
        ).where(
            VolunteerHoursLog.member_id == member_uuid,
            VolunteerHoursLog.date >= date_from.date(),
            VolunteerHoursLog.date <= date_to.date(),
        )
    )
    total = result.scalar() or 0.0

    return MemberVolunteerSummary(total_hours=float(total))
