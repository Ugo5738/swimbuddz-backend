"""Shared session access decisions.

This module is intentionally pure: callers supply the member payload, session
payload, and any cross-service context they already fetched (cohort enrollment,
pod roster, confirmed booking state). That keeps service boundaries intact
while giving every surface the same access rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

from libs.common.datetime_utils import utc_now

COMMUNITY = "community"
CLUB = "club"
ACADEMY = "academy"
COHORT_CLASS = "cohort_class"
EVENT = "event"
SCHEDULED = "scheduled"
SIGN_IN_WINDOW_BEFORE = timedelta(minutes=30)
SIGN_IN_WINDOW_AFTER = timedelta(minutes=60)
SIGN_IN_STATUSES = {SCHEDULED, "in_progress", "completed"}


@dataclass(frozen=True)
class SessionAccessDecision:
    """Access flags for different product surfaces."""

    required_tier: str
    visible: bool
    bookable: bool
    digest_eligible: bool
    prompt_eligible: bool
    sign_in_allowed: bool
    sign_in_eligible: bool = False
    reason: str | None = None
    access_source: str | None = None
    fee_amount_kobo: int | None = None
    price_label: str | None = None


DENIAL_MESSAGES: dict[str, str] = {
    "session_unavailable": "This session is not available for booking.",
    "membership_required": (
        "You need an active SwimBuddz membership to book this session."
    ),
    "club_required": "This session is available to active Club members.",
    "community_dropins_disabled": (
        "This Club session is Club-only and is not open for Community drop-ins."
    ),
    "cohort_required": (
        "This session is restricted to members enrolled in its academy cohort."
    ),
    "cohort_access_suspended": (
        "Your access to this cohort is currently suspended. Please contact "
        "the SwimBuddz team for more information."
    ),
    "pod_required": "This club session is restricted to its assigned pod.",
}


def _value(payload: Any, key: str, default: Any = None) -> Any:
    if isinstance(payload, Mapping):
        return payload.get(key, default)
    return getattr(payload, key, default)


def _normalized(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "value"):
        value = value.value
    return str(value).lower()


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def required_tier_for_session_type(session_type: Any) -> str:
    normalized = _normalized(session_type)
    if normalized in {ACADEMY, COHORT_CLASS}:
        return ACADEMY
    if normalized == CLUB:
        return CLUB
    return COMMUNITY


def member_declared_tiers(member: Any) -> set[str]:
    """Return declared compatibility labels without inventing programme access."""
    tiers = {
        _normalized(t) for t in (_value(member, "active_tiers") or []) if _normalized(t)
    }
    primary = _normalized(_value(member, "primary_tier"))
    if primary:
        tiers.add(primary)

    # These are independent products. The labels remain for compatibility,
    # but Academy must never manufacture Club and Club must never manufacture
    # annual Membership.
    if not tiers:
        tiers.add(COMMUNITY)
    return tiers


def has_active_paid_until(member: Any, field: str, now: datetime | None = None) -> bool:
    now = now or utc_now()
    paid_until = _parse_datetime(_value(member, field))
    return paid_until is not None and paid_until > now


def active_paid_tiers(member: Any, now: datetime | None = None) -> set[str]:
    """Return products backed by current paid entitlements.

    These are intentionally independent. Programme participation does not
    silently buy annual Community membership, and Academy does not silently
    buy Club. Any bundle/included policy must create the corresponding dated
    entitlement explicitly.
    """
    now = now or utc_now()
    tiers: set[str] = set()
    if has_active_paid_until(member, "academy_paid_until", now):
        tiers.add(ACADEMY)
    if has_active_paid_until(member, "club_paid_until", now) or has_active_paid_until(
        member, "post_academy_club_until", now
    ):
        tiers.add(CLUB)
    if has_active_paid_until(member, "community_paid_until", now):
        tiers.add(COMMUNITY)
    return tiers


def has_any_paid_entitlement(member: Any, now: datetime | None = None) -> bool:
    return bool(active_paid_tiers(member, now))


def default_booking_prompt_tier(member: Any, now: datetime | None = None) -> str:
    """Return the current highest paid tier used by booking-prompt cadence."""
    now = now or utc_now()
    if has_active_paid_until(member, "academy_paid_until", now):
        return ACADEMY
    if has_active_paid_until(member, "club_paid_until", now) or has_active_paid_until(
        member, "post_academy_club_until", now
    ):
        return CLUB
    if has_active_paid_until(member, "community_paid_until", now):
        return COMMUNITY
    return "prospect"


def has_paid_session_access(
    member: Any, session_type: Any, now: datetime | None = None
) -> bool:
    """Return whether the member has a paid entitlement for this session type."""
    now = now or utc_now()
    normalized = _normalized(session_type)
    paid_tiers = active_paid_tiers(member, now)

    if normalized == COMMUNITY or normalized == EVENT:
        return COMMUNITY in paid_tiers
    if normalized == CLUB:
        # Academy is a distinct programme, not an implicit Club purchase.
        # Dated ClubEnrollment checks are supplied by service adapters; this
        # pure compatibility helper only recognises explicit legacy Club
        # access and the post-Academy bridge.
        return has_active_paid_until(
            member, "club_paid_until", now
        ) or has_active_paid_until(member, "post_academy_club_until", now)
    if normalized in {ACADEMY, COHORT_CLASS}:
        return True
    return bool(paid_tiers)


def is_unpaid_community_prospect(member: Any, now: datetime | None = None) -> bool:
    return COMMUNITY in member_declared_tiers(member) and not has_any_paid_entitlement(
        member, now
    )


def evaluate_session_access(
    member: Any,
    session: Any,
    *,
    now: datetime | None = None,
    cohort_enrollment: Mapping[str, Any] | None = None,
    pod_member_ids: Iterable[Any] | None = None,
    confirmed_booking: bool = False,
    club_product_access: bool | None = None,
    club_access_result: Mapping[str, Any] | None = None,
) -> SessionAccessDecision:
    """Evaluate member access for a single session.

    The caller decides which flag it needs. For example, a digest should use
    ``digest_eligible`` while booking should use ``bookable``.
    """
    now = now or utc_now()
    session_type = _normalized(_value(session, "session_type"))
    required_tier = required_tier_for_session_type(session_type)
    status = _normalized(_value(session, "status")) or SCHEDULED
    starts_at = _parse_datetime(_value(session, "starts_at"))
    ends_at = _parse_datetime(_value(session, "ends_at"))

    is_scheduled = status == SCHEDULED
    not_started = starts_at is None or starts_at > now
    not_ended = ends_at is None or ends_at > now
    available_for_new_booking = is_scheduled and not_started
    available_for_digest = is_scheduled and not_ended
    status_allows_sign_in = status in SIGN_IN_STATUSES
    available_for_sign_in = (
        status_allows_sign_in
        and (starts_at is None or now >= starts_at - SIGN_IN_WINDOW_BEFORE)
        and (ends_at is None or now <= ends_at + SIGN_IN_WINDOW_AFTER)
    )

    if confirmed_booking:
        return SessionAccessDecision(
            required_tier=required_tier,
            visible=True,
            bookable=False,
            digest_eligible=available_for_digest,
            prompt_eligible=False,
            sign_in_allowed=available_for_sign_in,
            sign_in_eligible=status_allows_sign_in,
            reason=None if available_for_sign_in else "session_unavailable",
            access_source="confirmed_booking",
            fee_amount_kobo=0,
            price_label="Already booked",
        )

    allowed = False
    reason: str | None = None
    access_source: str | None = None
    fee_amount_kobo: int | None = None
    price_label: str | None = None
    entitlement_at = starts_at or now
    paid_tiers = active_paid_tiers(member, entitlement_at)

    if session_type == COHORT_CLASS:
        cohort_id = _value(session, "cohort_id")
        if not cohort_id:
            reason = "cohort_required"
        elif not cohort_enrollment or not cohort_enrollment.get("enrolled"):
            reason = "cohort_required"
        elif cohort_enrollment.get("access_suspended"):
            reason = "cohort_access_suspended"
        else:
            allowed = True
    elif session_type == CLUB:
        # Live service callers supply the authoritative, session-dated result
        # from members_service.  ``None`` preserves explicit legacy Club and
        # bridge access for older/offline callers, but deliberately does not
        # inherit Club from an Academy tier.
        if club_access_result is not None:
            has_club_access = bool(club_access_result.get("allowed"))
        else:
            has_club_access = (
                club_product_access
                if club_product_access is not None
                else has_active_paid_until(member, "club_paid_until", entitlement_at)
                or has_active_paid_until(
                    member, "post_academy_club_until", entitlement_at
                )
            )
        if not has_club_access:
            allows_dropins = _value(session, "allows_community_dropins", None)
            if allows_dropins is None:
                reason = "club_required"
            elif not bool(allows_dropins):
                reason = "community_dropins_disabled"
            elif COMMUNITY not in paid_tiers:
                reason = "membership_required"
            else:
                allowed = True
                access_source = "community_dropin"
                raw_dropin_fee = _value(session, "community_dropin_fee_kobo")
                fee_amount_kobo = int(raw_dropin_fee or 0)
                price_label = "Community drop-in rate"
        else:
            member_id = _value(member, "member_id") or _value(member, "id")
            pod_id = _value(session, "pod_id")
            if pod_id and pod_member_ids is not None:
                pod_ids = {str(mid) for mid in pod_member_ids}
                if str(member_id) not in pod_ids:
                    reason = "pod_required"
                else:
                    allowed = True
            else:
                allowed = True
            if allowed:
                source = (
                    str(club_access_result.get("source"))
                    if club_access_result is not None
                    else "legacy_club_entitlement"
                )
                access_source = source
                if source == "club_enrollment":
                    fee_amount_kobo = 0
                    price_label = "Included in Club quarter"
                elif source == "club_transition":
                    # Transition is an eligibility/payment mode, not a price.
                    # Resolve the session's current Admin-set price now; the
                    # booking/payment layer snapshots the resulting amount.
                    fee_amount_kobo = int(_value(session, "pool_fee", 0) or 0)
                    price_label = "2026 transition session price"
                else:
                    # Legacy and post-Academy bridges are eligibility grants,
                    # not prepaid quarters; the session's operational member
                    # rate is selected explicitly by this resolver.
                    fee_amount_kobo = int(_value(session, "pool_fee", 0) or 0)
                    price_label = "Club session rate"
    elif session_type in {COMMUNITY, EVENT}:
        if COMMUNITY in paid_tiers:
            allowed = True
            access_source = "community_membership"
            fee_amount_kobo = int(_value(session, "pool_fee", 0) or 0)
            price_label = "Session rate"
        else:
            reason = "membership_required"
    else:
        if COMMUNITY in paid_tiers:
            allowed = True
            access_source = "community_membership"
            fee_amount_kobo = int(_value(session, "pool_fee", 0) or 0)
            price_label = "Session rate"
        else:
            reason = "membership_required"

    if allowed and not available_for_new_booking:
        reason = "session_unavailable"

    # Prospect prompts are intentionally separate from direct booking prompts.
    prompt_eligible = allowed and available_for_digest

    return SessionAccessDecision(
        required_tier=required_tier,
        visible=allowed and available_for_digest,
        bookable=allowed and available_for_new_booking,
        digest_eligible=allowed and available_for_digest,
        prompt_eligible=prompt_eligible,
        sign_in_allowed=allowed and available_for_sign_in,
        sign_in_eligible=allowed and status_allows_sign_in,
        reason=None if allowed and available_for_new_booking else reason,
        access_source=access_source,
        fee_amount_kobo=fee_amount_kobo,
        price_label=price_label,
    )


def denial_message(reason: str | None) -> str:
    if not reason:
        return "This session is not available to your membership."
    return DENIAL_MESSAGES.get(
        reason, "This session is not available to your membership."
    )
