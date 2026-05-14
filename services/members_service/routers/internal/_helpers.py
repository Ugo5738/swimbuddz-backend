"""Shared helpers + module-level constants for the internal routes."""

from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

from services.members_service.models import MemberMembership

_VALID_TIERS = {"community", "club", "academy"}

_LAGOS_TZ = ZoneInfo("Africa/Lagos")
# Roles that should receive the daily birthday WhatsApp-shoutout reminder.
# Kept loose so any admin-flavoured role gets the reminder; tighten later
# once a dedicated "comms_admin" role is rolled out.
_ADMIN_REMINDER_ROLES = ("admin", "comms_admin", "community_manager")


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
