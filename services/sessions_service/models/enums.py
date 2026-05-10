"""Enum definitions for sessions service models."""

import enum


def enum_values(enum_cls):
    """Return persistent DB values for SAEnum mappings."""
    return [member.value for member in enum_cls]


class SessionLocation(str, enum.Enum):
    SUNFIT_POOL = "sunfit_pool"
    ROWE_PARK_POOL = "rowe_park_pool"
    FEDERAL_PALACE_POOL = "federal_palace_pool"
    OPEN_WATER = "open_water"
    OTHER = "other"


class SessionType(str, enum.Enum):
    COHORT_CLASS = "cohort_class"
    ONE_ON_ONE = "one_on_one"
    GROUP_BOOKING = "group_booking"
    CLUB = "club"
    COMMUNITY = "community"
    EVENT = "event"


class SessionStatus(str, enum.Enum):
    DRAFT = "draft"
    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class PodVisibility(str, enum.Enum):
    """Whether a pod appears in the public directory.

    `public` pods are listed for self-selection during registration and on
    the member dashboard. `private` pods are admin/coach-managed only.
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
    COACH_TRANSFER = "coach_transfer"  # Coach moved them from another pod
