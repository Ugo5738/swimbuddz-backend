"""
Business logic for member tier management.

Pure functions with no database dependencies for easy testing.
All datetime operations use timezone-aware UTC datetimes.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

# Tier priority for sorting and comparison
TIER_PRIORITY = {"academy": 3, "club": 2, "community": 1}


def normalize_member_tiers(
    current_tier: Optional[str],
    current_tiers: Optional[list[str]],
    community_paid_until: Optional[datetime],
    club_paid_until: Optional[datetime],
    academy_paid_until: Optional[datetime] = None,
) -> tuple[str, list[str], bool]:
    """
    Normalize membership tiers based on paid entitlements.

    Rules:
    - Everyone has community.
    - Add club if club_paid_until is in the future.
    - Add academy if academy_paid_until is in the future.
    - Sort by priority (academy > club > community).
    Returns (primary_tier, tiers_list, changed_flag).
    """
    now = datetime.now(timezone.utc)
    tiers = set(current_tiers or [])

    if current_tier:
        tiers.add(current_tier)

    # Apply entitlements based on payment dates
    if club_paid_until and club_paid_until > now:
        tiers.update({"club", "community"})
    if community_paid_until and community_paid_until > now:
        tiers.add("community")
    if academy_paid_until and academy_paid_until > now:
        tiers.update({"academy", "club", "community"})

    # Default to community if empty
    if not tiers:
        tiers.add("community")

    # Sort by priority (highest first)
    sorted_tiers = sorted(
        [t for t in tiers if t in TIER_PRIORITY],
        key=lambda t: TIER_PRIORITY[t],
        reverse=True,
    )

    # Determine if changes occurred
    old_tiers_set = set(current_tiers or [])
    new_primary = sorted_tiers[0] if sorted_tiers else "community"

    # Check if either the tier set changed or the primary tier changed
    tiers_changed = set(sorted_tiers) != old_tiers_set
    primary_changed = new_primary != current_tier

    changed = tiers_changed or primary_changed

    return new_primary, sorted_tiers, changed


def calculate_community_expiry(
    current_expiry: Optional[datetime],
    years: int,
) -> datetime:
    """
    Calculate new community tier expiry date.

    If the member has an active subscription, extends from current expiry.
    Otherwise, starts from now.

    Args:
        current_expiry: Current community_paid_until value
        years: Number of years to add (1-5)

    Returns:
        New expiry datetime
    """
    now = datetime.now(timezone.utc)
    base = current_expiry if current_expiry and current_expiry > now else now
    return base + timedelta(days=365 * years)


def calculate_club_expiry(
    current_expiry: Optional[datetime],
    months: int,
) -> datetime:
    """
    Calculate new club tier expiry date.

    If the member has an active subscription, extends from current expiry.
    Otherwise, starts from now.

    Args:
        current_expiry: Current club_paid_until value
        months: Number of months to add (1-12)

    Returns:
        New expiry datetime
    """
    now = datetime.now(timezone.utc)
    base = current_expiry if current_expiry and current_expiry > now else now
    return base + timedelta(days=30 * months)


def validate_club_readiness(
    emergency_contact_name: Optional[str],
    emergency_contact_relationship: Optional[str],
    emergency_contact_phone: Optional[str],
    location_preference: Optional[list[str]],
    time_of_day_availability: Optional[list[str]],
    availability_slots: Optional[list[str]],
) -> bool:
    """
    Check if member has completed club readiness requirements.

    Club members need to provide:
    - Emergency contact information
    - Location preferences
    - Availability information

    Args:
        emergency_contact_name: Name of emergency contact
        emergency_contact_relationship: Relationship to emergency contact
        emergency_contact_phone: Phone number of emergency contact
        location_preference: List of preferred locations
        time_of_day_availability: List of preferred times (morning, afternoon, etc.)
        availability_slots: List of available days/slots

    Returns:
        True if all required fields are filled
    """
    return bool(
        emergency_contact_name
        and emergency_contact_relationship
        and emergency_contact_phone
        and location_preference
        and len(location_preference) > 0
        and time_of_day_availability
        and len(time_of_day_availability) > 0
        and availability_slots
        and len(availability_slots) > 0
    )


def check_club_eligibility(
    approved_tiers: set[str],
    requested_tiers: set[str],
    readiness_complete: bool,
) -> tuple[bool, Optional[str]]:
    """
    Check if member is eligible for club activation.

    A member can activate club if:
    1. They already have club/academy approved, OR
    2. They have requested club/academy AND completed readiness

    Args:
        approved_tiers: Set of currently approved tiers
        requested_tiers: Set of requested (pending) tiers
        readiness_complete: Whether club readiness requirements are met

    Returns:
        Tuple of (is_eligible, error_message_or_none)
    """
    club_approved = "club" in approved_tiers or "academy" in approved_tiers
    club_requested = "club" in requested_tiers or "academy" in requested_tiers

    if club_approved:
        return True, None

    if not club_requested:
        return False, "Club upgrade not requested"

    if not readiness_complete:
        return False, "Club readiness is incomplete"

    return True, None


def merge_tiers_after_club_activation(
    current_tiers: Optional[list[str]],
    current_tier: Optional[str],
    requested_tiers: Optional[list[str]],
    was_already_approved: bool,
) -> tuple[list[str], str, Optional[list[str]]]:
    """
    Compute new tier state after club activation.

    Args:
        current_tiers: Currently approved tiers
        current_tier: Current primary tier
        requested_tiers: Currently requested tiers
        was_already_approved: True if club was already in approved_tiers

    Returns:
        Tuple of (new_tiers_list, new_primary_tier, remaining_requested_tiers)
    """
    # Build updated set
    approved = set(current_tiers or [])
    if current_tier:
        approved.add(current_tier)
    approved.update({"club", "community"})

    # Sort by priority
    sorted_tiers = sorted(
        [t for t in approved if t in TIER_PRIORITY],
        key=lambda t: TIER_PRIORITY[t],
        reverse=True,
    )

    # Determine new primary
    current_priority = TIER_PRIORITY.get(current_tier or "", 0)
    top_priority = TIER_PRIORITY.get(sorted_tiers[0], 0) if sorted_tiers else 0
    new_primary = (
        sorted_tiers[0]
        if top_priority > current_priority
        else (current_tier or sorted_tiers[0])
    )

    # Clean up requested tiers
    remaining = None
    if requested_tiers:
        if was_already_approved:
            # Clear any stale club/academy/community requests
            remaining = [
                t for t in requested_tiers if t not in {"club", "academy", "community"}
            ]
        else:
            # Only clear club/community since academy might still be pending
            remaining = [t for t in requested_tiers if t not in {"club", "community"}]
        remaining = remaining if remaining else None

    return sorted_tiers, new_primary, remaining
