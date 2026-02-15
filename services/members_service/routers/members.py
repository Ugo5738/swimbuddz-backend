"""Core members router - CRUD operations for member profiles."""

import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from libs.auth.dependencies import get_current_user, require_admin
from libs.auth.models import AuthUser
from libs.common.logging import get_logger
from libs.common.media_utils import resolve_media_url, resolve_media_urls
from libs.common.supabase import get_supabase_admin_client
from libs.db.session import get_async_db
from services.members_service.models import (
    CoachProfile,
    Member,
    MemberAvailability,
    MemberChallengeCompletion,
    MemberEmergencyContact,
    MemberMembership,
    MemberPreferences,
    MemberProfile,
    VolunteerInterest,
)
from services.members_service.routers._helpers import (
    member_eager_load_options,
    normalize_member_tiers,
    resolve_member_media_urls,
)
from services.members_service.schemas import (
    MemberBasicResponse,
    MemberCreate,
    MemberDirectoryResponse,
    MemberListResponse,
    MemberPublicResponse,
    MemberResponse,
    MemberUpdate,
)
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)
router = APIRouter(prefix="/members", tags=["members"])


@router.get("/me", response_model=MemberResponse)
async def get_current_member_profile(
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Get the profile of the currently authenticated member."""
    query = (
        select(Member)
        .where(Member.auth_id == current_user.user_id)
        .options(*member_eager_load_options())
    )
    result = await db.execute(query)
    member = result.scalar_one_or_none()

    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member profile not found. Please complete registration.",
        )

    if normalize_member_tiers(member):
        db.add(member)
        await db.commit()
        await db.refresh(member)

    # Resolve media URLs
    member_dict = MemberResponse.model_validate(member).model_dump()
    member_dict = await resolve_member_media_urls(member_dict)
    return member_dict


@router.patch("/me", response_model=MemberResponse)
async def update_current_member(
    member_in: MemberUpdate,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Update the currently authenticated member's profile.
    Handles nested updates for profile, membership, preferences, etc.
    """
    query = (
        select(Member)
        .where(Member.auth_id == current_user.user_id)
        .options(*member_eager_load_options())
    )
    result = await db.execute(query)
    member = result.scalar_one_or_none()

    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member profile not found",
        )

    update_data = member_in.model_dump(exclude_unset=True)

    logger.warning(f"PATCH /me received update_data: {update_data}")
    if "profile_photo_media_id" in update_data:
        logger.warning(
            f"profile_photo_media_id value: {update_data['profile_photo_media_id']}"
        )
    else:
        logger.warning("profile_photo_media_id NOT in update_data")

    # Extract nested updates
    profile_update = update_data.pop("profile", None)
    emergency_contact_update = update_data.pop("emergency_contact", None)
    availability_update = update_data.pop("availability", None)
    membership_update = update_data.pop("membership", None)
    preferences_update = update_data.pop("preferences", None)

    # Update core Member fields
    for field, value in update_data.items():
        if hasattr(member, field):
            logger.warning(f"Setting member.{field} = {value}")
            setattr(member, field, value)

    # Update profile sub-record
    if profile_update and member.profile:
        if "address" not in profile_update and "area_in_lagos" in profile_update:
            profile_update["address"] = profile_update.get("area_in_lagos")
        if "area_in_lagos" not in profile_update and "address" in profile_update:
            profile_update["area_in_lagos"] = profile_update.get("address")
        for field, value in profile_update.items():
            if value is not None and hasattr(member.profile, field):
                setattr(member.profile, field, value)

    # Update emergency contact sub-record
    if emergency_contact_update and member.emergency_contact:
        for field, value in emergency_contact_update.items():
            if value is not None and hasattr(member.emergency_contact, field):
                setattr(member.emergency_contact, field, value)

    # Update availability sub-record
    if availability_update and member.availability:
        for field, value in availability_update.items():
            if value is not None and hasattr(member.availability, field):
                setattr(member.availability, field, value)

    # Update membership sub-record (with protection for billing fields)
    if membership_update and member.membership:
        protected_fields = {
            "community_paid_until",
            "club_paid_until",
            "academy_paid_until",
            "academy_alumni",
            "primary_tier",
            "active_tiers",
        }
        for field, value in membership_update.items():
            if (
                field not in protected_fields
                and value is not None
                and hasattr(member.membership, field)
            ):
                setattr(member.membership, field, value)

        # Handle tier change requests
        requested_tiers = membership_update.get("requested_tiers")
        if requested_tiers is not None:
            current_tiers = member.membership.active_tiers or []
            if set(requested_tiers) != set(current_tiers):
                member.membership.requested_tiers = requested_tiers

    # Update preferences sub-record
    if preferences_update and member.preferences:
        for field, value in preferences_update.items():
            if value is not None and hasattr(member.preferences, field):
                setattr(member.preferences, field, value)

    db.add(member)
    await db.commit()
    await db.refresh(member)

    query = (
        select(Member)
        .where(Member.id == member.id)
        .options(*member_eager_load_options())
    )
    result = await db.execute(query)
    updated_member = result.scalar_one()

    # Resolve media URLs
    member_dict = MemberResponse.model_validate(updated_member).model_dump()
    member_dict = await resolve_member_media_urls(member_dict)
    return member_dict


@router.post("/", response_model=MemberResponse, status_code=status.HTTP_201_CREATED)
async def create_member(
    member_in: MemberCreate,
    db: AsyncSession = Depends(get_async_db),
):
    """
    Directly create a member (internal use or admin).
    Normal users should go through the pending registration flow.
    """
    query = select(Member).where(Member.email == member_in.email)
    result = await db.execute(query)
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        )

    member = Member(**member_in.model_dump())
    db.add(member)
    await db.commit()
    await db.refresh(member)

    query = (
        select(Member)
        .where(Member.id == member.id)
        .options(*member_eager_load_options())
    )
    result = await db.execute(query)
    return result.scalar_one()


@router.get("/public", response_model=List[MemberPublicResponse])
async def list_public_members(
    db: AsyncSession = Depends(get_async_db),
):
    """
    List all members for public dropdown (no auth required).
    Returns limited info (id, first_name, last_name).
    """
    query = select(Member).order_by(Member.first_name, Member.last_name)
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/directory", response_model=List[MemberDirectoryResponse])
async def list_directory_members(
    db: AsyncSession = Depends(get_async_db),
):
    """
    List members who opted into the community directory.
    Filters server-side by show_in_directory=True.
    Returns only the fields needed for the directory page.
    No auth required — directory is a community feature.
    """
    from sqlalchemy.orm import selectinload

    query = (
        select(Member)
        .join(MemberProfile, MemberProfile.member_id == Member.id)
        .where(MemberProfile.show_in_directory.is_(True))
        .where(Member.is_active.is_(True))
        .where(Member.registration_complete.is_(True))
        .options(selectinload(Member.profile))
        .order_by(Member.first_name, Member.last_name)
    )
    result = await db.execute(query)
    members = result.scalars().all()

    # Batch-resolve profile photo URLs
    media_ids = [m.profile_photo_media_id for m in members if m.profile_photo_media_id]
    url_map = await resolve_media_urls(media_ids) if media_ids else {}

    responses: list[MemberDirectoryResponse] = []
    for member in members:
        profile = member.profile
        responses.append(
            MemberDirectoryResponse(
                id=member.id,
                first_name=member.first_name,
                last_name=member.last_name,
                profile_photo_url=(
                    url_map.get(str(member.profile_photo_media_id))
                    if member.profile_photo_media_id
                    else None
                ),
                city=profile.city if profile else None,
                country=profile.country if profile else None,
                swim_level=profile.swim_level if profile else None,
                interest_tags=profile.interest_tags if profile else None,
            )
        )
    return responses


@router.post("/bulk-basic", response_model=dict[str, MemberBasicResponse])
async def get_members_bulk_basic(
    member_ids: list[uuid.UUID],
    db: AsyncSession = Depends(get_async_db),
):
    """
    Bulk lookup of basic member info by IDs.

    Internal endpoint for service-to-service calls. Returns a dict mapping
    member_id (string) -> basic info (name, email, profile photo).
    Max 50 IDs per request.
    """
    if len(member_ids) > 50:
        raise HTTPException(
            status_code=400, detail="Maximum 50 member IDs per request."
        )
    if not member_ids:
        return {}

    query = select(Member).where(Member.id.in_(member_ids))
    result = await db.execute(query)
    members = result.scalars().all()

    # Resolve profile photo URLs via media service
    photo_ids = [m.profile_photo_media_id for m in members if m.profile_photo_media_id]
    url_map = await resolve_media_urls(photo_ids) if photo_ids else {}

    response = {}
    for m in members:
        photo_url = (
            url_map.get(str(m.profile_photo_media_id))
            if m.profile_photo_media_id
            else None
        )
        response[str(m.id)] = MemberBasicResponse(
            id=m.id,
            first_name=m.first_name,
            last_name=m.last_name,
            email=m.email,
            profile_photo_media_id=m.profile_photo_media_id,
            profile_photo_url=photo_url,
        )
    return response


@router.get("/public/{member_id}")
async def get_member_for_verification(
    member_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    """
    Get member data for public verification (e.g., pool staff scanning QR code).
    Returns limited info for verification purposes only.
    No authentication required.
    """
    query = (
        select(Member)
        .where(Member.id == member_id)
        .options(*member_eager_load_options())
    )
    result = await db.execute(query)
    member = result.scalar_one_or_none()

    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found",
        )

    # Resolve profile photo URL
    profile_photo_url = await resolve_media_url(member.profile_photo_media_id)

    # Return only necessary verification data
    membership = member.membership
    return {
        "id": str(member.id),
        "first_name": member.first_name,
        "last_name": member.last_name,
        "email": member.email,  # For staff to verify identity
        "profile_photo_url": profile_photo_url,
        "created_at": member.created_at.isoformat() if member.created_at else None,
        "membership": (
            {
                "active_tiers": membership.active_tiers if membership else [],
                "community_paid_until": (
                    membership.community_paid_until.isoformat()
                    if membership and membership.community_paid_until
                    else None
                ),
                "club_paid_until": (
                    membership.club_paid_until.isoformat()
                    if membership and membership.club_paid_until
                    else None
                ),
                "academy_paid_until": (
                    membership.academy_paid_until.isoformat()
                    if membership and membership.academy_paid_until
                    else None
                ),
            }
            if membership
            else None
        ),
    }


@router.get("/", response_model=List[MemberListResponse])
async def list_members(
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_async_db),
):
    """List all members (admin use)."""
    query = (
        select(Member)
        .options(*member_eager_load_options())
        .offset(skip)
        .limit(limit)
        .order_by(Member.created_at.desc())
    )
    result = await db.execute(query)
    members = result.scalars().all()

    # Collect all media IDs for batch resolution
    media_ids = [m.profile_photo_media_id for m in members if m.profile_photo_media_id]
    url_map = await resolve_media_urls(media_ids) if media_ids else {}

    responses: list[MemberListResponse] = []
    for member in members:
        base = MemberResponse.model_validate(member, from_attributes=True)
        payload = base.model_dump(exclude={"coach_profile"})
        payload["is_coach"] = bool(member.coach_profile)
        # Add resolved URL
        if member.profile_photo_media_id:
            payload["profile_photo_url"] = url_map.get(member.profile_photo_media_id)
        responses.append(MemberListResponse(**payload))
    return responses


@router.get("/stats")
async def get_member_stats(
    db: AsyncSession = Depends(get_async_db),
):
    """Get member statistics."""
    query = select(func.count(Member.id))
    result = await db.execute(query)
    total_members = result.scalar_one() or 0

    query = select(func.count(Member.id)).where(Member.registration_complete.is_(True))
    result = await db.execute(query)
    active_members = result.scalar_one() or 0

    query = select(func.count(Member.id)).where(Member.approval_status == "approved")
    result = await db.execute(query)
    approved_members = result.scalar_one() or 0

    query = select(func.count(Member.id)).where(Member.approval_status == "pending")
    result = await db.execute(query)
    pending_approvals = result.scalar_one() or 0

    return {
        "total_members": total_members,
        "active_members": active_members,
        "approved_members": approved_members,
        "pending_approvals": pending_approvals,
    }


@router.get("/by-auth/{auth_id}", response_model=MemberResponse)
async def get_member_by_auth_id(
    auth_id: str,
    db: AsyncSession = Depends(get_async_db),
):
    """
    Get a member by their Supabase auth_id.
    Used for service-to-service lookups (e.g., payments service).
    """
    query = (
        select(Member)
        .where(Member.auth_id == auth_id)
        .options(*member_eager_load_options())
    )
    result = await db.execute(query)
    member = result.scalar_one_or_none()

    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found",
        )

    return member


@router.get("/{member_id}", response_model=MemberResponse)
async def get_member(
    member_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Get a member by ID (admin use)."""
    query = (
        select(Member)
        .where(Member.id == member_id)
        .options(*member_eager_load_options())
    )
    result = await db.execute(query)
    member = result.scalar_one_or_none()

    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found",
        )

    # Resolve media URLs
    member_dict = MemberResponse.model_validate(member).model_dump()
    member_dict = await resolve_member_media_urls(member_dict)
    return member_dict


@router.patch("/{member_id}", response_model=MemberResponse)
async def update_member(
    member_id: uuid.UUID,
    member_in: MemberUpdate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Update a member by ID (admin only)."""
    query = (
        select(Member)
        .where(Member.id == member_id)
        .options(*member_eager_load_options())
    )
    result = await db.execute(query)
    member = result.scalar_one_or_none()

    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found",
        )

    update_data = member_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(member, field, value)

    db.add(member)
    await db.commit()
    await db.refresh(member)
    query = (
        select(Member)
        .where(Member.id == member.id)
        .options(*member_eager_load_options())
    )
    result = await db.execute(query)
    updated_member = result.scalar_one()

    # Resolve media URLs
    member_dict = MemberResponse.model_validate(updated_member).model_dump()
    member_dict = await resolve_member_media_urls(member_dict)
    return member_dict


@router.delete("/{member_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_member(
    member_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Delete a member by ID (admin only)."""
    query = select(Member).where(Member.id == member_id)
    result = await db.execute(query)
    member = result.scalar_one_or_none()

    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found",
        )

    # Delete from Supabase Auth
    try:
        supabase = get_supabase_admin_client()
        if member.auth_id:
            supabase.auth.admin.delete_user(member.auth_id)
            logger.info(
                "Deleted Supabase user",
                extra={"extra_fields": {"auth_id": member.auth_id}},
            )
    except Exception as e:
        logger.error(
            "Failed to delete Supabase user",
            extra={"extra_fields": {"auth_id": member.auth_id, "error": str(e)}},
        )

    # Delete all related sub-tables
    await db.execute(delete(MemberProfile).where(MemberProfile.member_id == member.id))
    await db.execute(
        delete(MemberEmergencyContact).where(
            MemberEmergencyContact.member_id == member.id
        )
    )
    await db.execute(
        delete(MemberAvailability).where(MemberAvailability.member_id == member.id)
    )
    await db.execute(
        delete(MemberMembership).where(MemberMembership.member_id == member.id)
    )
    await db.execute(
        delete(MemberPreferences).where(MemberPreferences.member_id == member.id)
    )
    await db.execute(delete(CoachProfile).where(CoachProfile.member_id == member.id))
    await db.execute(
        delete(VolunteerInterest).where(VolunteerInterest.member_id == member.id)
    )
    await db.execute(
        delete(MemberChallengeCompletion).where(
            MemberChallengeCompletion.member_id == member.id
        )
    )

    await db.delete(member)
    await db.commit()
    return None
