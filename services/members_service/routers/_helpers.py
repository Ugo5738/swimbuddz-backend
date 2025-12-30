"""Shared helper functions for members service routers."""

from sqlalchemy.orm import selectinload

from services.members_service import service as member_service
from services.members_service.models import Member


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
