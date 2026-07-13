"""Display-oriented membership status normalization.

The stored tier fields have different meanings:
- ``active_tiers``/``primary_tier`` describe approval/admin lifecycle.
- ``requested_tiers`` describes an upgrade request.
- ``*_paid_until`` is the paid entitlement source of truth.

This module turns those raw fields into a compact API contract that member
surfaces can render without re-implementing business semantics.
"""

from __future__ import annotations

from datetime import datetime
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


def build_membership_status_summary(
    *,
    primary_tier: Optional[str],
    active_tiers: Optional[list[str]],
    requested_tiers: Optional[list[str]],
    community_paid_until: Optional[datetime],
    club_paid_until: Optional[datetime],
    academy_paid_until: Optional[datetime],
    pending_payment_reference: Optional[str] = None,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Build canonical display/access status fields for one membership row."""

    now = now or utc_now()
    declared_tiers = _normalize_tiers(active_tiers)
    if primary := _normalize_tier(primary_tier):
        declared_tiers.add(primary)
    requested = _normalize_tiers(requested_tiers)
    paid_until = {
        "community": community_paid_until,
        "club": club_paid_until,
        "academy": academy_paid_until,
    }
    direct_paid = {tier: _paid_until_is_active(paid_until[tier], now) for tier in TIERS}

    inherited_from: dict[str, Optional[str]] = {tier: None for tier in TIERS}
    paid_tiers: set[str] = set()
    if direct_paid["academy"]:
        paid_tiers.update(TIERS)
        inherited_from["club"] = "academy" if not direct_paid["club"] else None
        inherited_from["community"] = (
            "academy" if not direct_paid["community"] else None
        )
    if direct_paid["club"]:
        paid_tiers.update({"club", "community"})
        if not direct_paid["community"] and inherited_from["community"] is None:
            inherited_from["community"] = "club"
    if direct_paid["community"]:
        paid_tiers.add("community")

    has_pending_payment = bool(pending_payment_reference)
    tier_statuses: dict[str, dict[str, Any]] = {}
    for tier in TIERS:
        declared = tier in declared_tiers
        is_requested = tier in requested
        is_direct_paid = direct_paid[tier]
        inherited = tier in paid_tiers and not is_direct_paid

        if tier in paid_tiers:
            status = "active"
        elif has_pending_payment and (is_requested or declared):
            status = "payment_pending"
        elif is_requested:
            status = "requested"
        elif _paid_until_is_expired(paid_until[tier], now):
            status = "expired"
        elif declared:
            status = "approved_unpaid"
        else:
            status = "inactive"

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
        }

    sorted_paid_tiers = _sort_tiers(paid_tiers)
    paid_tier = sorted_paid_tiers[0] if sorted_paid_tiers else "prospect"
    display_label = _display_label(paid_tier, tier_statuses)

    return {
        "paid_tier": paid_tier,
        "paid_tiers": sorted_paid_tiers,
        "display_label": display_label,
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
