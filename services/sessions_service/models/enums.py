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
    # A1 Phase 3.1 (2026-05-17): dropped ONE_ON_ONE and GROUP_BOOKING — they
    # were aspirational slots (zero rows in production, no member-facing
    # booking flow). Private 1-on-1 and small-group academy instruction is
    # now expressed via Cohort.type (CohortType.PRIVATE, SMALL_GROUP,
    # CORPORATE). See docs/design/A1_SESSION_DISCRIMINATOR_REFACTOR.md.
    # The corresponding Postgres enum values may remain in
    # session_type_enum harmlessly; no row references them.
    COHORT_CLASS = "cohort_class"
    CLUB = "club"
    COMMUNITY = "community"
    EVENT = "event"


class SessionStatus(str, enum.Enum):
    DRAFT = "draft"
    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


# Pod-related enums (PodVisibility, PodStatus, PodAssignmentSource) moved
# to services.members_service.models.enums in May 2026 alongside the Pod
# model itself. See docs/club/POD_OPERATIONS.md.
