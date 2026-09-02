"""Display-oriented membership status normalization.

The stored tier fields have different meanings:
- ``active_tiers``/``primary_tier`` are legacy cached fields and must not be
  used as an authorization source.
- ``requested_tiers`` describes an upgrade request.
- ``*_paid_until`` is the paid entitlement source of truth.

This module turns those raw fields into a compact API contract that member
surfaces can render without re-implementing business semantics.
"""

from __future__ import annotations

from datetime import datetime
from math import ceil
from typing import Any, Optional

from libs.common.datetime_utils import utc_now

TIERS: tuple[str, ...] = ("community", "club", "academy")
TIER_PRIORITY = {"academy": 3, "club": 2, "community": 1}
TIER_LABELS = {
    "community": "Community",
    "club": "Club",
    "academy": "Academy",
}
STATUS_LABELS = {
    "active": "Active",
    "payment_pending": "Payment pending",
    "requested": "Requested",
    "approved_unpaid": "Approved, payment needed",
    "expired": "Expired",
    "inactive": "Inactive",
}
DISPLAY_SUFFIXES = {
    "payment_pending": "Payment Pending",
    "requested": "Pending",
    "approved_unpaid": "Payment Needed",
    "expired": "Expired",
}


def _normalize_tier(value: Any) -> Optional[str]:
    tier = str(value or "").strip().lower()
    return tier if tier in TIER_PRIORITY else None


def _normalize_tiers(values: Any) -> set[str]:
    if not values:
        return set()
    if isinstance(values, str):
        values = [values]
    return {tier for value in values if (tier := _normalize_tier(value))}


def _paid_until_is_active(paid_until: Optional[datetime], now: datetime) -> bool:
    return bool(paid_until and paid_until > now)


def _paid_until_is_expired(paid_until: Optional[datetime], now: datetime) -> bool:
    return bool(paid_until and paid_until <= now)


def _sort_tiers(tiers: set[str]) -> list[str]:
    return sorted(tiers, key=lambda tier: TIER_PRIORITY[tier], reverse=True)


def effective_tiers_from_dates(
    *,
    community_paid_until: Optional[datetime],
    club_paid_until: Optional[datetime],
    academy_paid_until: Optional[datetime],
    post_academy_club_until: Optional[datetime] = None,
    now: Optional[datetime] = None,
) -> set[str]:
    """Return independent legacy product labels from canonical dates.

    These labels remain in the API for compatibility. They are not a product
    hierarchy: Academy does not imply Club, and neither programme implies an
    annual SwimBuddz Membership.
    """
    now = now or utc_now()
    tiers: set[str] = set()
    if _paid_until_is_active(academy_paid_until, now):
        tiers.add("academy")
    if _paid_until_is_active(club_paid_until, now) or _paid_until_is_active(
        post_academy_club_until, now
    ):
        tiers.add("club")
    if _paid_until_is_active(community_paid_until, now):
        tiers.add("community")
    return tiers


def build_membership_status_summary(
    *,
    primary_tier: Optional[str],
    active_tiers: Optional[list[str]],
    declared_tiers: Optional[list[str]] = None,
    requested_tiers: Optional[list[str]],
    community_paid_until: Optional[datetime],
    club_paid_until: Optional[datetime],
    academy_paid_until: Optional[datetime],
    post_academy_club_until: Optional[datetime] = None,
    pending_payment_reference: Optional[str] = None,
    pending_tier_payments: Optional[dict[str, str]] = None,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Build canonical display/access status fields for one membership row."""

    now = now or utc_now()
    lifecycle_tiers = _normalize_tiers(declared_tiers)
    if not lifecycle_tiers:
        lifecycle_tiers = _normalize_tiers(active_tiers)
        if primary := _normalize_tier(primary_tier):
            lifecycle_tiers.add(primary)
    requested = _normalize_tiers(requested_tiers)
    paid_until = {
        "community": community_paid_until,
        "club": club_paid_until,
        "academy": academy_paid_until,
    }
    # Every approved membership row has Community identity, even when payment
    # has lapsed. Historical entitlement dates preserve higher-tier lifecycle
    # context after the effective cache has been normalized to Prospect.
    lifecycle_tiers.add("community")
    lifecycle_tiers.update(
        tier for tier, entitlement_end in paid_until.items() if entitlement_end
    )
    if post_academy_club_until:
        lifecycle_tiers.add("club")
    direct_paid = {tier: _paid_until_is_active(paid_until[tier], now) for tier in TIERS}
    bridge_active = _paid_until_is_active(post_academy_club_until, now)

    inherited_from: dict[str, Optional[str]] = {tier: None for tier in TIERS}
    paid_tiers: set[str] = {
        tier for tier, is_paid in direct_paid.items() if is_paid
    }
    if bridge_active:
        paid_tiers.add("club")
        if not direct_paid["club"] and inherited_from["club"] is None:
            inherited_from["club"] = "post_academy"

    # ``pending_payment_reference`` is retained only for legacy checkout
    # resumption. Display state is driven by the tier-scoped map so unrelated
    # session/transport payments cannot change membership labels.
    pending_tiers = _normalize_tiers((pending_tier_payments or {}).keys())
    has_pending_payment = bool(pending_tiers)
    tier_statuses: dict[str, dict[str, Any]] = {}
    for tier in TIERS:
        declared = tier in lifecycle_tiers
        is_requested = tier in requested
        is_direct_paid = direct_paid[tier]
        inherited = tier in paid_tiers and not is_direct_paid

        if tier in paid_tiers:
            status = "active"
        elif tier in pending_tiers:
            status = "payment_pending"
        elif is_requested:
            status = "requested"
        elif _paid_until_is_expired(paid_until[tier], now):
            status = "expired"
        elif declared:
            status = "approved_unpaid"
        else:
            status = "inactive"

        effective_candidates = [paid_until[tier]]
        if tier == "club":
            effective_candidates.append(post_academy_club_until)
        effective_dates = [
            value for value in effective_candidates if value and value > now
        ]
        effective_until = max(effective_dates) if effective_dates else None
        days_remaining = (
            max(0, ceil((effective_until - now).total_seconds() / 86400))
            if effective_until
            else None
        )
        reminder_window = 30 if tier == "community" else 14

        tier_statuses[tier] = {
            "tier": tier,
            "status": status,
            "label": STATUS_LABELS[status],
            "paid_until": paid_until[tier],
            "requested": is_requested,
            "declared_active": declared,
            "direct_paid": is_direct_paid,
            "inherited": inherited,
            "inherited_from": inherited_from[tier] if inherited else None,
            "effective_until": effective_until,
            "expiring_soon": bool(
                status == "active"
                and days_remaining is not None
                and days_remaining <= reminder_window
            ),
            "days_remaining": days_remaining,
            "access_source": (
                "direct"
                if is_direct_paid
                else inherited_from[tier]
                if inherited
                else None
            ),
        }

    sorted_paid_tiers = _sort_tiers(paid_tiers)
    paid_tier = sorted_paid_tiers[0] if sorted_paid_tiers else "prospect"
    display_label = _display_label(paid_tier, tier_statuses)
    display_detail = _display_detail(paid_tier, tier_statuses)

    return {
        "declared_tiers": _sort_tiers(lifecycle_tiers),
        "effective_paid_tiers": sorted_paid_tiers,
        "highest_paid_tier": paid_tier,
        # Compatibility aliases. New authorization code should use the
        # explicit effective/highest names above.
        "paid_tier": paid_tier,
        "paid_tiers": sorted_paid_tiers,
        "display_label": display_label,
        "display_detail": display_detail,
        "payment_pending": has_pending_payment,
        "tier_statuses": tier_statuses,
    }


def _display_label(paid_tier: str, tier_statuses: dict[str, dict[str, Any]]) -> str:
    if paid_tier != "prospect":
        return f"{TIER_LABELS[paid_tier]} Member"

    for status in ("payment_pending", "requested", "expired", "approved_unpaid"):
        tiers = {
            tier
            for tier, tier_status in tier_statuses.items()
            if tier_status["status"] == status
        }
        if tiers:
            tier = _sort_tiers(tiers)[0]
            return f"{TIER_LABELS[tier]} ({DISPLAY_SUFFIXES[status]})"

    return "Prospect"


def _display_detail(
    paid_tier: str, tier_statuses: dict[str, dict[str, Any]]
) -> Optional[str]:
    """Describe the most important non-active lifecycle beside a paid tier."""

    if paid_tier == "prospect":
        return None

    for status in ("payment_pending", "requested", "expired", "approved_unpaid"):
        tiers = {
            tier
            for tier, tier_status in tier_statuses.items()
            if tier_status["status"] == status
        }
        if tiers:
            tier = _sort_tiers(tiers)[0]
            noun = (
                "request"
                if status == "requested"
                else "payment"
                if status == "payment_pending"
                else "access"
            )
            return f"{TIER_LABELS[tier]} {noun}: {STATUS_LABELS[status]}"

    return None
