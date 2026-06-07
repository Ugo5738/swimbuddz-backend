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


class SessionBookingStatus(str, enum.Enum):
    """Lifecycle of a SessionBooking (the *intent* to attend a session).

    Terminal at session start time — what happened at the session
    (PRESENT / ABSENT / LATE / EXCUSED) lives on AttendanceRecord in
    attendance_service, not here. See
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


# Pod-related enums (PodVisibility, PodStatus, PodAssignmentSource) moved
# to services.members_service.models.enums in May 2026 alongside the Pod
# model itself. See docs/club/POD_OPERATIONS.md.


# ============================================================================
# Make-up scheduling (Phase 0) — see
# docs/design/AVAILABILITY_AND_MAKEUP_SCHEDULING_DESIGN.md §6b
# ============================================================================


class MakeupStatus(str, enum.Enum):
    """Lifecycle of a MakeupBooking (individual-learner make-up / reschedule)."""

    REQUESTED = "requested"  # learner asked (self-serve); awaiting confirm
    HELD = "held"  # slot soft-held pending admin confirmation
    CONFIRMED = "confirmed"  # admin confirmed; make-up session created/linked
    COMPLETED = "completed"  # make-up attended (PRESENT/LATE)
    FORFEITED = "forfeited"  # late-cancel / no-show with no grace left
    EXPIRED = "expired"  # window passed unbooked
    CANCELLED = "cancelled"  # admin cancelled


class MakeupOrigin(str, enum.Enum):
    """Why a make-up exists."""

    LEARNER_RESCHEDULE = "learner_reschedule"  # learner-initiated (>=24h notice)
    EXCUSED_ABSENCE = "excused_absence"  # coach marked EXCUSED
    SESSION_CANCELLED = "session_cancelled"  # a scheduled session was cancelled
    LATE_JOIN = "late_join"  # enrolled after sessions began


class MakeupLearnerType(str, enum.Enum):
    """Which learner population the make-up belongs to."""

    COHORT = "cohort"  # academy cohort learner (Phase 1)
    ONE_ON_ONE = "one_on_one"  # individual 1:1 learner (Phase 2)


class MakeupBlockKind(str, enum.Enum):
    """What the make-up's grace/window 'block' is anchored to."""

    COHORT_TERM = "cohort_term"  # academy cohort enrollment term
    LESSON_PACKAGE = "lesson_package"  # purchased N-session bundle (1:1)
