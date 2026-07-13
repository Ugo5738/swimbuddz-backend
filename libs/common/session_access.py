"""Shared session access decisions.

This module is intentionally pure: callers supply the member payload, session
payload, and any cross-service context they already fetched (cohort enrollment,
pod roster, confirmed booking state). That keeps service boundaries intact
while giving every surface the same access rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from libs.common.datetime_utils import utc_now

COMMUNITY = "community"
CLUB = "club"
ACADEMY = "academy"
COHORT_CLASS = "cohort_class"
EVENT = "event"
SCHEDULED = "scheduled"


@dataclass(frozen=True)
class SessionAccessDecision:
    """Access flags for different product surfaces."""

    required_tier: str
    visible: bool
    bookable: bool
    digest_eligible: bool
    prompt_eligible: bool
    sign_in_allowed: bool
    reason: str | None = None


DENIAL_MESSAGES: dict[str, str] = {
    "session_unavailable": "This session is not available for booking.",
    "membership_required": (
        "You need an active SwimBuddz membership to book this session."
    ),
    "club_required": "This session is available to active Club members.",
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
    """Return declared tiers from member payload, with hierarchy expanded."""
    tiers = {
        _normalized(t) for t in (_value(member, "active_tiers") or []) if _normalized(t)
    }
    primary = _normalized(_value(member, "primary_tier"))
    if primary:
        tiers.add(primary)

    if ACADEMY in tiers:
        tiers.update({CLUB, COMMUNITY})
    if CLUB in tiers:
        tiers.add(COMMUNITY)
    if not tiers:
        tiers.add(COMMUNITY)
    return tiers


def has_active_paid_until(member: Any, field: str, now: datetime | None = None) -> bool:
    now = now or utc_now()
    paid_until = _parse_datetime(_value(member, field))
    return paid_until is not None and paid_until > now


def active_paid_tiers(member: Any, now: datetime | None = None) -> set[str]:
    """Return tiers backed by current paid entitlements."""
    now = now or utc_now()
    tiers: set[str] = set()
    if has_active_paid_until(member, "academy_paid_until", now):
        tiers.update({ACADEMY, CLUB, COMMUNITY})
    if has_active_paid_until(member, "club_paid_until", now):
        tiers.update({CLUB, COMMUNITY})
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
    if has_active_paid_until(member, "club_paid_until", now):
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
        return bool(paid_tiers)
    if normalized == CLUB:
        return CLUB in paid_tiers
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

    if confirmed_booking:
        return SessionAccessDecision(
            required_tier=required_tier,
            visible=True,
            bookable=False,
            digest_eligible=available_for_digest,
            prompt_eligible=False,
            sign_in_allowed=True,
        )

    allowed = False
    reason: str | None = None
    paid_tiers = active_paid_tiers(member, now)

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
        if CLUB not in paid_tiers:
            reason = "club_required"
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
    elif session_type in {COMMUNITY, EVENT}:
        if paid_tiers:
            allowed = True
        else:
            reason = "membership_required"
    else:
        if paid_tiers:
            allowed = True
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
        sign_in_allowed=allowed,
        reason=None if allowed and available_for_new_booking else reason,
    )


def denial_message(reason: str | None) -> str:
    if not reason:
        return "This session is not available to your membership."
    return DENIAL_MESSAGES.get(
        reason, "This session is not available to your membership."
    )
