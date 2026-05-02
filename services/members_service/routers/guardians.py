"""Guardian-link router.

Two surfaces:
  * Admin (``/admin/members/guardians/*``) — admins create, verify, list, and
    deactivate guardian links.
  * Internal (``/internal/members/guardians/*``) — consumed by chat_service
    and other services that need to enforce safeguarding rules. Not proxied
    by the gateway.

Phase 0 scope: minimal admin CRUD + one internal read endpoint. Self-service
flows (a parent claiming their child, verification emails, etc.) are Phase 1.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from libs.db.session import get_async_db
from services.members_service.models import GuardianLink, GuardianRelationship, Member

admin_router = APIRouter(prefix="/admin/members/guardians", tags=["admin-guardians"])
internal_router = APIRouter(
    prefix="/internal/members/guardians", tags=["internal-guardians"]
)

logger = get_logger(__name__)


# ==========================================================================
# Schemas
# ==========================================================================


class GuardianLinkCreate(BaseModel):
    minor_member_id: uuid.UUID
    guardian_member_id: uuid.UUID
    relationship: GuardianRelationship
    notes: Optional[str] = Field(default=None, max_length=2000)


class GuardianLinkUpdate(BaseModel):
    is_active: Optional[bool] = None
    verified: Optional[bool] = None  # set True to mark verified_at = now()
    notes: Optional[str] = Field(default=None, max_length=2000)


class GuardianLinkResponse(BaseModel):
    id: uuid.UUID
    minor_member_id: uuid.UUID
    guardian_member_id: uuid.UUID
    relationship: GuardianRelationship
    is_active: bool
    verified_at: Optional[datetime]
    verified_by: Optional[uuid.UUID]
    notes: Optional[str]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ==========================================================================
# Admin endpoints
# ==========================================================================


@admin_router.post(
    "",
    response_model=GuardianLinkResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_guardian_link(
    payload: GuardianLinkCreate,
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
) -> GuardianLinkResponse:
    """Create a new guardian link between a minor and an adult member.

    The link is created unverified — admin must call PATCH with
    ``verified=true`` after confirming the relationship.
    """
    if payload.minor_member_id == payload.guardian_member_id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Minor and guardian must be distinct members.",
        )

    # Confirm both members exist
    result = await db.execute(
        select(Member).where(
            Member.id.in_({payload.minor_member_id, payload.guardian_member_id})
        )
    )
    members = result.scalars().all()
    if len(members) != 2:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "One or both members not found.",
        )

    # Enforce at application level that an active pair doesn't already exist
    # (the partial unique index will catch races, but an explicit check gives
    # a cleaner error).
    existing = await db.execute(
        select(GuardianLink).where(
            and_(
                GuardianLink.minor_member_id == payload.minor_member_id,
                GuardianLink.guardian_member_id == payload.guardian_member_id,
                GuardianLink.is_active.is_(True),
            )
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "An active guardian link already exists for this pair.",
        )

    link = GuardianLink(
        minor_member_id=payload.minor_member_id,
        guardian_member_id=payload.guardian_member_id,
        relationship=payload.relationship,
        notes=payload.notes,
    )
    db.add(link)
    await db.commit()
    await db.refresh(link)
    logger.info(
        "Created guardian_link id=%s minor=%s guardian=%s",
        link.id,
        link.minor_member_id,
        link.guardian_member_id,
    )
    return GuardianLinkResponse.model_validate(link)


@admin_router.get("", response_model=list[GuardianLinkResponse])
async def list_guardian_links(
    minor_member_id: Optional[uuid.UUID] = Query(default=None),
    guardian_member_id: Optional[uuid.UUID] = Query(default=None),
    active_only: bool = Query(default=True),
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
) -> list[GuardianLinkResponse]:
    """List guardian links, filtered by minor_member_id or guardian_member_id.

    At least one filter must be provided to avoid accidentally dumping the
    whole table.
    """
    if not minor_member_id and not guardian_member_id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Provide minor_member_id or guardian_member_id.",
        )

    conditions = []
    if minor_member_id:
        conditions.append(GuardianLink.minor_member_id == minor_member_id)
    if guardian_member_id:
        conditions.append(GuardianLink.guardian_member_id == guardian_member_id)
    if active_only:
        conditions.append(GuardianLink.is_active.is_(True))

    result = await db.execute(select(GuardianLink).where(and_(*conditions)))
    links = result.scalars().all()
    return [GuardianLinkResponse.model_validate(lnk) for lnk in links]


@admin_router.patch("/{link_id}", response_model=GuardianLinkResponse)
async def update_guardian_link(
    link_id: uuid.UUID,
    payload: GuardianLinkUpdate,
    admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
) -> GuardianLinkResponse:
    """Update a guardian link — verify, deactivate, or edit notes.

    ``verified=true`` sets ``verified_at = now()`` and records the admin's
    member_id in ``verified_by``. Once verified, the link can gate
    safeguarding-sensitive chat operations (e.g. joining a coach-minor DM).
    """
    result = await db.execute(select(GuardianLink).where(GuardianLink.id == link_id))
    link = result.scalar_one_or_none()
    if link is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Guardian link not found.")

    if payload.is_active is not None:
        link.is_active = payload.is_active
    if payload.notes is not None:
        link.notes = payload.notes
    if payload.verified is True and link.verified_at is None:
        link.verified_at = utc_now()
        # `admin.member_id` is the member_id of the admin user — attribute name
        # follows project convention on AuthUser; see libs/auth/models.py.
        admin_mid = getattr(admin, "member_id", None)
        if admin_mid is not None:
            try:
                link.verified_by = uuid.UUID(str(admin_mid))
            except (ValueError, TypeError):
                link.verified_by = None

    await db.commit()
    await db.refresh(link)
    return GuardianLinkResponse.model_validate(link)


# ==========================================================================
# Internal endpoints (service-to-service; not proxied by gateway)
# ==========================================================================


@internal_router.get(
    "/for-minor/{minor_member_id}", response_model=list[GuardianLinkResponse]
)
async def get_guardians_for_minor(
    minor_member_id: uuid.UUID,
    verified_only: bool = Query(default=True),
    db: AsyncSession = Depends(get_async_db),
) -> list[GuardianLinkResponse]:
    """Return active (and by default verified) guardians for a given minor.

    Consumed by chat_service to enforce: a coach cannot be in a 1:1 DM with a
    minor without a verified guardian present.
    """
    conditions = [
        GuardianLink.minor_member_id == minor_member_id,
        GuardianLink.is_active.is_(True),
    ]
    if verified_only:
        conditions.append(GuardianLink.verified_at.is_not(None))

    result = await db.execute(select(GuardianLink).where(and_(*conditions)))
    links = result.scalars().all()
    return [GuardianLinkResponse.model_validate(lnk) for lnk in links]
