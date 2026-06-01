"""
Business logic for member tier management.

Pure functions with no database dependencies for easy testing.
All datetime operations use timezone-aware UTC datetimes.
"""

from datetime import datetime
from typing import Optional

from dateutil.relativedelta import relativedelta

from libs.common.datetime_utils import utc_now

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
    Compute member tier state derived from paid entitlements.

    Tiers are computed FRESH from ``*_paid_until`` columns each call — stored
    ``current_tiers`` and ``current_tier`` are ignored as authoritative inputs
    and only used to detect whether the result is a change. Per the tier
    hierarchy in docs/club/PRICING_STRATEGY.md every tier includes the ones
    below it, so an active club grants community and an active academy
    grants club + community.

    Rules:
    - Add academy + club + community if academy_paid_until is in the future.
    - Add club + community if club_paid_until is in the future.
    - Add community if community_paid_until is in the future.
    - If nothing paid, fall back to community as the persistent baseline (matches
      "where club members land if they pause" framing in PRICING_STRATEGY.md).
    - Sort by priority (academy > club > community).
    - Expired tiers are stripped — this is the source of truth, not the
      stored value.

    Returns (primary_tier, tiers_list, changed_flag).
    """
    now = utc_now()
    tiers: set[str] = set()

    if academy_paid_until and academy_paid_until > now:
        tiers.update({"academy", "club", "community"})
    if club_paid_until and club_paid_until > now:
        tiers.update({"club", "community"})
    if community_paid_until and community_paid_until > now:
        tiers.add("community")

    if not tiers:
        tiers.add("community")

    sorted_tiers = sorted(
        [t for t in tiers if t in TIER_PRIORITY],
        key=lambda t: TIER_PRIORITY[t],
        reverse=True,
    )

    new_primary = sorted_tiers[0] if sorted_tiers else "community"

    tiers_changed = set(sorted_tiers) != set(current_tiers or [])
    primary_changed = new_primary != current_tier
    changed = tiers_changed or primary_changed

    return new_primary, sorted_tiers, changed


def calculate_community_expiry(
    current_expiry: Optional[datetime],
    years: int,
) -> datetime:
    """Calculate new community tier expiry date.

    Calendar-correct: uses ``relativedelta(years=N)`` instead of
    ``timedelta(days=365 * N)``. Active subscriptions extend from the current
    expiry; lapsed/never-paid members start from now. Critically this means
    cycle changes (e.g. switch from quarterly→annual mid-cycle) accumulate
    fairly — the member doesn't lose the time they already paid for, and the
    new period is an exact calendar offset of the base.
    """
    now = utc_now()
    base = current_expiry if current_expiry and current_expiry > now else now
    return base + relativedelta(years=years)


def academy_bundle_expiry(
    now: datetime,
    community_paid_until: Optional[datetime],
    club_paid_until: Optional[datetime],
) -> tuple[datetime, datetime]:
    """Community + Club expiry granted by an academy payment.

    Academy enrollment bundles Community (1 year) and Club (3 months) access,
    whether paid in full or by installment (docs/club/PRICING_STRATEGY.md tier
    hierarchy: academy ⊃ club ⊃ community). Each entitlement is floored to
    ``now + duration`` and never shortened below an existing, longer date — so
    repeated installment payments are idempotent and a member who already paid
    further out keeps their longer access.

    Returns ``(community_paid_until, club_paid_until)``.
    """
    community_floor = now + relativedelta(years=1)
    club_floor = now + relativedelta(months=3)
    new_community = (
        community_floor
        if community_paid_until is None or community_paid_until < community_floor
        else community_paid_until
    )
    new_club = (
        club_floor
        if club_paid_until is None or club_paid_until < club_floor
        else club_paid_until
    )
    return new_community, new_club


def calculate_club_expiry(
    current_expiry: Optional[datetime],
    months: int,
) -> datetime:
    """Calculate new club tier expiry date.

    Calendar-correct: uses ``relativedelta(months=N)`` instead of
    ``timedelta(days=30 * N)``. Active subscriptions extend from the current
    expiry; lapsed/never-paid members start from now. Cycle changes
    (quarterly → biannual → annual) accumulate as exact calendar months on
    top of the prior expiry, fixing the audit Path 6 concern that the old
    30-day approximation was systematically short by 1-5 days per year.
    """
    now = utc_now()
    base = current_expiry if current_expiry and current_expiry > now else now
    return base + relativedelta(months=months)


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
