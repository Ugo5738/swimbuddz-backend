"""Enum definitions for volunteer service models."""

import enum


def enum_values(enum_cls):
    """Return persistent DB values for SAEnum mappings."""
    return [member.value for member in enum_cls]


class VolunteerRoleCategory(str, enum.Enum):
    SESSION_LEAD = "session_lead"
    WARMUP_LEAD = "warmup_lead"
    LANE_MARSHAL = "lane_marshal"
    CHECKIN = "checkin"
    SAFETY = "safety"
    WELCOME = "welcome"
    RIDE_SHARE = "ride_share"
    MENTOR = "mentor"
    MEDIA = "media"
    GALLERY_SUPPORT = "gallery_support"
    EVENTS_LOGISTICS = "events_logistics"
    TRIP_PLANNER = "trip_planner"
    ACADEMY_ASSISTANT = "academy_assistant"
    OTHER = "other"


class VolunteerTier(str, enum.Enum):
    TIER_1 = "tier_1"
    TIER_2 = "tier_2"
    TIER_3 = "tier_3"


class OpportunityStatus(str, enum.Enum):
    DRAFT = "draft"
    OPEN = "open"
    FILLED = "filled"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class OpportunityType(str, enum.Enum):
    OPEN_CLAIM = "open_claim"
    APPROVAL_REQUIRED = "approval_required"


class SlotStatus(str, enum.Enum):
    CLAIMED = "claimed"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    NO_SHOW = "no_show"
    COMPLETED = "completed"


class RecognitionTier(str, enum.Enum):
    BRONZE = "bronze"
    SILVER = "silver"
    GOLD = "gold"


class RewardType(str, enum.Enum):
    DISCOUNTED_SESSION = "discounted_session"
    FREE_MERCH = "free_merch"
    PRIORITY_EVENT = "priority_event"
    MEMBERSHIP_DISCOUNT = "membership_discount"
    CUSTOM = "custom"
