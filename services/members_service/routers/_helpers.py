"""Shared helper functions for members service routers."""

from libs.common.media_utils import resolve_media_urls
from services.members_service import service as member_service
from services.members_service.models import Member
from sqlalchemy.orm import selectinload


def member_eager_load_options():
    """Return selectinload options for all Member relationships."""
    return [
        selectinload(Member.profile),
        selectinload(Member.emergency_contact),
        selectinload(Member.availability),
        selectinload(Member.membership),
        selectinload(Member.preferences),
        selectinload(Member.coach_profile),
    ]


def normalize_member_tiers(member: Member) -> bool:
    """
    Ensure membership tiers reflect active entitlements.
    Returns True if a change was made.

    Works with the decomposed Member model (membership sub-table).
    """
    if not member.membership:
        return False

    m = member.membership
    primary, tiers, changed = member_service.normalize_member_tiers(
        current_tier=m.primary_tier,
        current_tiers=m.active_tiers,
        community_paid_until=m.community_paid_until,
        club_paid_until=m.club_paid_until,
        academy_paid_until=m.academy_paid_until,
    )
    if changed:
        m.primary_tier = primary
        m.active_tiers = tiers
    return changed


async def sync_member_roles(member: Member, current_user, db) -> bool:
    """
    Merge roles from Supabase JWT (app_metadata.roles) into Member.roles.
    Returns True if a change was made.
    """
    desired_roles = set(current_user.roles or [])
    existing_roles = set(member.roles or [])
    merged = existing_roles | desired_roles
    if merged != existing_roles:
        member.roles = list(merged) if merged else None
        await db.flush()
        return True
    return False


async def resolve_member_media_urls(member_data: dict) -> dict:
    """
    Resolve media IDs to URLs in a member response dict.

    Args:
        member_data: Dictionary of member data (from model_dump or dict conversion)

    Returns:
        Enriched member_data with URL fields populated
    """

    # Collect all media IDs that need resolution
    media_ids = []
    if member_data.get("profile_photo_media_id"):
        media_ids.append(member_data["profile_photo_media_id"])

    # Also check coach_profile if present
    coach = member_data.get("coach_profile")
    if coach:
        if coach.get("coach_profile_photo_media_id"):
            media_ids.append(coach["coach_profile_photo_media_id"])
        if coach.get("background_check_document_media_id"):
            media_ids.append(coach["background_check_document_media_id"])

    if not media_ids:
        return member_data

    # Resolve all URLs via HTTP call to media service
    url_map = await resolve_media_urls(media_ids)

    # Populate URL fields
    if member_data.get("profile_photo_media_id"):
        member_data["profile_photo_url"] = url_map.get(
            member_data["profile_photo_media_id"]
        )

    if coach:
        if coach.get("coach_profile_photo_media_id"):
            coach["coach_profile_photo_url"] = url_map.get(
                coach["coach_profile_photo_media_id"]
            )
        if coach.get("background_check_document_media_id"):
            coach["background_check_document_url"] = url_map.get(
                coach["background_check_document_media_id"]
            )

    return member_data
