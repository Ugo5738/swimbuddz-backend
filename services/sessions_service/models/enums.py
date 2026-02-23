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
