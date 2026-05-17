"""Enum definitions for attendance service models."""

import enum


def enum_values(enum_cls):
    """Return persistent DB values for SAEnum mappings."""
    return [member.value for member in enum_cls]


class AttendanceStatus(str, enum.Enum):
    PRESENT = "present"
    ABSENT = "absent"
    LATE = "late"
    EXCUSED = "excused"
    CANCELLED = "cancelled"


class AttendanceRole(str, enum.Enum):
    SWIMMER = "swimmer"
    COACH = "coach"
    VOLUNTEER = "volunteer"
    GUEST = "guest"


class SessionBookingStatus(str, enum.Enum):
    """Lifecycle of a SessionBooking (the *intent* to attend).

    Terminal at session start time — post-session outcome (PRESENT/ABSENT/
    etc.) lives on AttendanceRecord, not here. See
    docs/design/A1_SESSION_DISCRIMINATOR_REFACTOR.md §C.
    """

    PENDING = "pending"  # awaiting payment / approval
    CONFIRMED = "confirmed"  # paid / approved; capacity held
    CANCELLED = "cancelled"  # member or admin cancelled before session
    EXPIRED = "expired"  # PENDING booking aged out without confirmation


class BookingChannel(str, enum.Enum):
    """How a SessionBooking was created."""

    MEMBER_SELF = "member_self"  # member booked directly
    ADMIN = "admin"  # admin booked on behalf of member
    CORPORATE_BULK = "corporate_bulk"  # corporate-wellness bulk booking
    BUNDLE_CART = "bundle_cart"  # paid via the multi-session bundle cart
