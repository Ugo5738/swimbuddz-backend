"""Enum definitions for members service models."""

import enum


def enum_values(enum_cls):
    """Return persistent DB values for SAEnum mappings."""
    return [member.value for member in enum_cls]


class CoachGrade(str, enum.Enum):
    """Coach grade levels based on credentials and experience."""

    GRADE_1 = "grade_1"
    GRADE_2 = "grade_2"
    GRADE_3 = "grade_3"


class AcquisitionSource(str, enum.Enum):
    """Where a member first heard about / joined SwimBuddz.

    Used by reporting_service to compute funnel-conversion snapshots
    broken down by acquisition channel.
    """

    SOCIAL_INSTAGRAM = "social_instagram"
    SOCIAL_TIKTOK = "social_tiktok"
    REFERRAL_MEMBER = "referral_member"
    REFERRAL_FRIEND = "referral_friend"
    CORPORATE = "corporate"
    EVENT = "event"
    WHATSAPP = "whatsapp"
    SEARCH = "search"
    OTHER = "other"


# ─── Pod enums ────────────────────────────────────────────────────────


class PodVisibility(str, enum.Enum):
    """Whether a pod appears in the public directory.

    `public` pods are listed for self-selection during registration and on
    the member dashboard. `private` pods are admin-managed only.
    """

    PUBLIC = "public"
    PRIVATE = "private"


class PodStatus(str, enum.Enum):
    """Lifecycle marker. Active pods accept members and surface in the
    review queue at the end of each 3-month cycle. Inactive pods are
    dissolved — chat archives, no new joins."""

    ACTIVE = "active"
    INACTIVE = "inactive"


class PodAssignmentSource(str, enum.Enum):
    """How a member came to be in a pod. Useful for understanding
    self-selection vs administrative-assignment behaviour."""

    ADMIN = "admin"  # Admin manually placed the member
    SELF = "self"  # Member self-selected (dashboard or registration)
    LEAD_TRANSFER = "lead_transfer"  # Pod Lead moved them from another pod


class DayOfWeek(str, enum.Enum):
    """Day-of-week for a pod's default session schedule. Stored as the
    standard 3-letter abbreviation so it round-trips cleanly to JSON
    and reads naturally in admin tooling."""

    MON = "mon"
    TUE = "tue"
    WED = "wed"
    THU = "thu"
    FRI = "fri"
    SAT = "sat"
    SUN = "sun"
