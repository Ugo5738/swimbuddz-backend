"""Enum definitions for academy service models."""

import enum


def enum_values(enum_cls):
    """Return persistent DB values for SAEnum mappings."""
    return [member.value for member in enum_cls]


class ProgramLevel(str, enum.Enum):
    BEGINNER_1 = "beginner_1"
    BEGINNER_2 = "beginner_2"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"
    SPECIALTY = "specialty"


class BillingType(str, enum.Enum):
    ONE_TIME = "one_time"
    SUBSCRIPTION = "subscription"
    PER_SESSION = "per_session"


class CohortStatus(str, enum.Enum):
    OPEN = "open"
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class CohortType(str, enum.Enum):
    """How the cohort is structured.

    The session layer (``SessionType.COHORT_CLASS`` with ``cohort_id`` set)
    is identical for all CohortType values — variation lives here on the
    cohort row rather than at the per-session level. See
    docs/design/A1_SESSION_DISCRIMINATOR_REFACTOR.md §B.
    """

    GROUP = "group"  # 8–12 student cohort (default; current behaviour)
    PRIVATE = "private"  # 1 student; member-paid 1-on-1 academy program
    SMALL_GROUP = "small_group"  # 2–6 students; member-specified group (friends/family)
    CORPORATE = "corporate"  # Commissioned by an organisation; capacity set by sponsor


class LocationType(str, enum.Enum):
    POOL = "pool"
    OPEN_WATER = "open_water"
    REMOTE = "remote"


class EnrollmentStatus(str, enum.Enum):
    PENDING_APPROVAL = "pending_approval"
    ENROLLED = "enrolled"
    WAITLIST = "waitlist"
    DROPOUT_PENDING = "dropout_pending"
    DROPPED = "dropped"
    GRADUATED = "graduated"


class EnrollmentSource(str, enum.Enum):
    WEB = "web"
    ADMIN = "admin"
    PARTNER = "partner"


# PaymentStatus is the canonical cross-service payment lifecycle enum and
# lives in libs/common/enums.py. Academy only writes PENDING / PAID / WAIVED /
# FAILED today; the canonical class also defines PENDING_REVIEW (used by
# payments_service) but academy's ``academy_payment_status_enum`` DB type
# does not carry that value, so academy code must not assign it without an
# accompanying migration.
from libs.common.enums import PaymentStatus  # noqa: F401


class InstallmentStatus(str, enum.Enum):
    PENDING = "pending"
    PAID = "paid"
    MISSED = "missed"
    WAIVED = "waived"


class MilestoneType(str, enum.Enum):
    SKILL = "skill"
    ENDURANCE = "endurance"
    TECHNIQUE = "technique"
    ASSESSMENT = "assessment"


class RequiredEvidence(str, enum.Enum):
    NONE = "none"
    VIDEO = "video"
    TIME_TRIAL = "time_trial"


class ProgressStatus(str, enum.Enum):
    PENDING = "pending"
    ACHIEVED = "achieved"


class MilestoneEventType(str, enum.Enum):
    CLAIMED = "claimed"
    APPROVED = "approved"
    REJECTED = "rejected"
    STATUS_CHANGED = "status_changed"
    # OVERRIDE rows record an admin (or AI) reversing a prior decision.
    # They are distinguished from a normal review by ``override_of_event_id``
    # pointing at the earlier event and ``override_reason`` being non-null.
    # ``actor_role`` may be ``"admin"`` or ``"ai_service"``; ``new_status``
    # carries what the override decided.
    OVERRIDE = "override"


class ResourceSourceType(str, enum.Enum):
    URL = "url"
    UPLOAD = "upload"


class ResourceVisibility(str, enum.Enum):
    PUBLIC = "public"
    ENROLLED_ONLY = "enrolled_only"
    COACHES_ONLY = "coaches_only"


class ExtensionRequestStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ProgramCategory(str, enum.Enum):
    LEARN_TO_SWIM = "learn_to_swim"
    SPECIAL_POPULATIONS = "special_populations"
    INSTITUTIONAL = "institutional"
    COMPETITIVE_ELITE = "competitive_elite"
    CERTIFICATIONS = "certifications"
    SPECIALIZED_DISCIPLINES = "specialized_disciplines"
    ADJACENT_SERVICES = "adjacent_services"


class CoachGrade(str, enum.Enum):
    GRADE_1 = "grade_1"
    GRADE_2 = "grade_2"
    GRADE_3 = "grade_3"


class CoachAssignmentRole(str, enum.Enum):
    LEAD = "lead"
    ASSISTANT = "assistant"
    SHADOW = "shadow"
    OBSERVER = "observer"


class CoachAssignmentStatus(str, enum.Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class ShadowEvaluationRecommendation(str, enum.Enum):
    CONTINUE_SHADOW = "continue_shadow"
    READY_FOR_ASSISTANT = "ready_for_assistant"
    READY_FOR_LEAD = "ready_for_lead"
