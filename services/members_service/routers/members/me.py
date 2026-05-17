"""Self-service member endpoints (/me*)."""

"""Core members router - CRUD operations for member profiles."""

from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from libs.auth.dependencies import get_current_user
from libs.auth.models import AuthUser
from libs.common.logging import get_logger
from libs.common.media_utils import resolve_media_urls
from libs.db.session import get_async_db
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.members_service.models import (
    ChallengeBadgeAward,
    Member,
)
from services.members_service.routers._helpers import (
    member_eager_load_options,
    normalize_member_tiers,
    resolve_member_media_urls,
)
from services.members_service.schemas import (
    ChallengeBadgeAwardResponse,
    MemberResponse,
    MemberUpdate,
)

logger = get_logger(__name__)
router = APIRouter()


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


@router.get("/me/badges", response_model=List[ChallengeBadgeAwardResponse])
async def list_my_badges(
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """List challenge badges earned by the authenticated member.

    Reads from the denormalised challenge_badge_awards table (one row per
    earned badge). Hydrates badge_image_url via media_service so the
    profile page can render the badge artwork without a per-row HTTP call.
    """
    member_row = await db.execute(
        select(Member).where(Member.auth_id == current_user.user_id)
    )
    member = member_row.scalar_one_or_none()
    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member profile not found.",
        )

    rows = await db.execute(
        select(ChallengeBadgeAward)
        .where(
            ChallengeBadgeAward.member_id == member.id,
            # Hide badges revoked by HQ. The row stays in the DB for audit
            # but doesn't surface on the member's profile/public pages.
            ChallengeBadgeAward.revoked_at.is_(None),
        )
        .order_by(ChallengeBadgeAward.awarded_at.desc())
    )
    awards = list(rows.scalars().all())

    # Bulk-resolve all distinct badge image media_ids in one HTTP call
    image_ids = [a.badge_image_media_id for a in awards if a.badge_image_media_id]
    url_map = await resolve_media_urls(image_ids) if image_ids else {}

    out: List[ChallengeBadgeAwardResponse] = []
    for award in awards:
        item = ChallengeBadgeAwardResponse.model_validate(award)
        if award.badge_image_media_id is not None:
            item.badge_image_url = url_map.get(
                award.badge_image_media_id
            ) or url_map.get(str(award.badge_image_media_id))
        out.append(item)
    return out


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
