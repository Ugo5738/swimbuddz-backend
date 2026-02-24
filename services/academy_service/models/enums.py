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


class PaymentStatus(str, enum.Enum):
    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"
    WAIVED = "waived"


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


class ResourceSourceType(str, enum.Enum):
    URL = "url"
    UPLOAD = "upload"


class ResourceVisibility(str, enum.Enum):
    PUBLIC = "public"
    ENROLLED_ONLY = "enrolled_only"
    COACHES_ONLY = "coaches_only"


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
