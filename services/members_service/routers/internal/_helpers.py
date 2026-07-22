"""Shared helpers + module-level constants for the internal routes."""

from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

from services.members_service.models import Member, MemberMembership
from services.members_service.services.member_service import normalize_member_tiers
from services.members_service.services.membership_status import (
    build_membership_status_summary,
)

_VALID_TIERS = {"community", "club", "academy"}

_LAGOS_TZ = ZoneInfo("Africa/Lagos")
# Roles that should receive the daily birthday WhatsApp-shoutout reminder.
# Kept loose so any admin-flavoured role gets the reminder; tighten later
# once a dedicated "comms_admin" role is rolled out.
_ADMIN_REMINDER_ROLES = ("admin", "comms_admin", "community_manager")


def _membership_fields(member: Member) -> dict:
    """Return the complete normalized tier contract for an internal member read."""
    membership = member.membership
    if not membership:
        primary_tier, active_tiers, _ = normalize_member_tiers(
            current_tier="community",
            current_tiers=["community"],
            community_paid_until=None,
            club_paid_until=None,
            academy_paid_until=None,
        )
        return {
            "primary_tier": primary_tier,
            "active_tiers": active_tiers,
            "declared_tiers": ["community"],
            "effective_paid_tiers": [],
            "highest_paid_tier": "prospect",
            "community_paid_until": None,
            "club_paid_until": None,
            "academy_paid_until": None,
        }

    normalize_kwargs = dict(
        current_tier=membership.primary_tier,
        current_tiers=membership.active_tiers,
        community_paid_until=membership.community_paid_until,
        club_paid_until=membership.club_paid_until,
        academy_paid_until=membership.academy_paid_until,
    )
    post_academy_club_until = getattr(membership, "post_academy_club_until", None)
    if hasattr(membership, "post_academy_club_until"):
        normalize_kwargs["post_academy_club_until"] = post_academy_club_until

    primary_tier, active_tiers, changed = normalize_member_tiers(**normalize_kwargs)
    if changed:
        membership.primary_tier = primary_tier
        membership.active_tiers = active_tiers

    summary = build_membership_status_summary(
        primary_tier=membership.primary_tier,
        active_tiers=membership.active_tiers,
        declared_tiers=membership.declared_tiers,
        requested_tiers=membership.requested_tiers,
        community_paid_until=membership.community_paid_until,
        club_paid_until=membership.club_paid_until,
        academy_paid_until=membership.academy_paid_until,
        post_academy_club_until=post_academy_club_until,
        pending_payment_reference=membership.pending_payment_reference,
        pending_tier_payments=membership.pending_tier_payments,
    )
    fields = {
        "primary_tier": primary_tier,
        "active_tiers": active_tiers,
        "declared_tiers": summary["declared_tiers"],
        "effective_paid_tiers": summary["effective_paid_tiers"],
        "highest_paid_tier": summary["highest_paid_tier"],
        "community_paid_until": (
            membership.community_paid_until.isoformat()
            if membership.community_paid_until
            else None
        ),
        "club_paid_until": (
            membership.club_paid_until.isoformat()
            if membership.club_paid_until
            else None
        ),
        "academy_paid_until": (
            membership.academy_paid_until.isoformat()
            if membership.academy_paid_until
            else None
        ),
    }
    if hasattr(membership, "post_academy_club_until"):
        fields["post_academy_club_until"] = (
            post_academy_club_until.isoformat() if post_academy_club_until else None
        )
    return fields


def _age_on(dob: datetime, on: date) -> int:
    """Whole-year age on the given date, in the member's local birthday sense."""
    born = dob.date() if isinstance(dob, datetime) else dob
    years = on.year - born.year
    if (on.month, on.day) < (born.month, born.day):
        years -= 1
    return max(0, years)


def _date_window_to_datetimes(start: date, end: date) -> tuple[datetime, datetime]:
    """Convert inclusive date window to UTC datetime [start_of_day, end_of_day]."""
    start_dt = datetime.combine(start, time.min, tzinfo=timezone.utc)
    end_dt = datetime.combine(end, time.max, tzinfo=timezone.utc)
    return start_dt, end_dt


def _tier_paid_until_column(tier: str):
    """Return the SQLAlchemy column tracking access end for the given tier."""
    return {
        "community": MemberMembership.community_paid_until,
        "club": MemberMembership.club_paid_until,
        "academy": MemberMembership.academy_paid_until,
    }[tier]
