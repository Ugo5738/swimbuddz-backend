"""Enum definitions for communications service models."""

import enum


def enum_values(enum_cls):
    """Return persistent DB values for SAEnum mappings."""
    return [member.value for member in enum_cls]


class AnnouncementCategory(str, enum.Enum):
    RAIN_UPDATE = "rain_update"
    SCHEDULE_CHANGE = "schedule_change"
    ACADEMY_UPDATE = "academy_update"
    EVENT = "event"
    COMPETITION = "competition"
    GENERAL = "general"
    CUSTOM = "custom"


class AnnouncementStatus(str, enum.Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class AnnouncementAudience(str, enum.Enum):
    COMMUNITY = "community"
    CLUB = "club"
    ACADEMY = "academy"


class MessageRecipientType(str, enum.Enum):
    COHORT = "cohort"
    STUDENT = "student"


class SessionNotificationType(str, enum.Enum):
    SESSION_PUBLISHED = "session_published"
    REMINDER_24H = "reminder_24h"
    REMINDER_3H = "reminder_3h"
    REMINDER_1H = "reminder_1h"
    SESSION_CANCELLED = "session_cancelled"
    SESSION_UPDATED = "session_updated"
    SPOTS_AVAILABLE = "spots_available"


class ScheduledNotificationStatus(str, enum.Enum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    CANCELLED = "cancelled"
