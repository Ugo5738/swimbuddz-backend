"""Sessions Service models package.

Note: Pod / PodAssignment moved to members_service in May 2026 — pods are
member groupings, not events. Sessions service reads pod data over HTTP
when needed. See docs/club/POD_OPERATIONS.md.
"""

from services.sessions_service.models.booking import SessionBooking
from services.sessions_service.models.core import (
    Session,
    SessionBundleCart,
    SessionCoach,
    SessionLocation,
    SessionStatus,
    SessionTemplate,
    SessionType,
)
from services.sessions_service.models.enums import (
    BookingChannel,
    SessionBookingStatus,
)

__all__ = [
    "BookingChannel",
    "Session",
    "SessionBooking",
    "SessionBookingStatus",
    "SessionBundleCart",
    "SessionCoach",
    "SessionLocation",
    "SessionStatus",
    "SessionTemplate",
    "SessionType",
]
