"""Enums for the Chat Service models.

Values are lowercase strings (matches repo convention: see wallet_service/models/enums.py).
"""

import enum


def enum_values(enum_cls):
    """Return persistent DB values for SAEnum mappings."""
    return [member.value for member in enum_cls]


class ChannelType(str, enum.Enum):
    """Three channel primitives per design doc §3."""

    GROUP = "group"
    BROADCAST = "broadcast"
    DIRECT = "direct"


class ParentEntityType(str, enum.Enum):
    """What a channel's membership is derived from. See design §4.1."""

    COHORT = "cohort"  # Academy cohort
    POD = "pod"  # Club pod (sub-group of ~5)
    EVENT = "event"  # Community event
    TRIP = "trip"  # Transport ride-share trip
    LOCATION = "location"  # Location/community channel (e.g. Lagos)
    ROLE = "role"  # Role-scoped broadcasts, alumni, etc.
    NONE = "none"  # Ad-hoc admin-created channel or support DM


class ChannelMemberRole(str, enum.Enum):
    """Per-channel role. See design §5.1."""

    OBSERVER = "observer"  # Read-only (muted state, safeguarding admin)
    MEMBER = "member"  # Standard participant
    MODERATOR = "moderator"  # Member + delete others, remove members, pin
    ADMIN = "admin"  # Moderator + edit settings, add, archive


class MembershipDerivation(str, enum.Enum):
    """How a member came to be in a channel. See design §4.2."""

    ENROLLMENT = "enrollment"
    RSVP = "rsvp"
    POD_ASSIGNMENT = "pod_assignment"
    TRIP_BOOKING = "trip_booking"
    ROLE = "role"
    MANUAL = "manual"


class RetentionPolicy(str, enum.Enum):
    """Per-channel retention policy. See design §9."""

    COHORT = "cohort"
    POD = "pod"
    EVENT = "event"
    TRIP = "trip"
    LOCATION = "location"
    ALUMNI = "alumni"
    COACH_PARENT_DM = "coach_parent_dm"
    SUPPORT_DM = "support_dm"


class SafeguardingReviewState(str, enum.Enum):
    """Moderation state of a message. See design §4.1."""

    NONE = "none"
    FLAGGED = "flagged"
    REVIEWED_OK = "reviewed_ok"
    REVIEWED_ACTIONED = "reviewed_actioned"


class ReportReason(str, enum.Enum):
    """Why a member reported a message. See design §4.1."""

    SAFEGUARDING = "safeguarding"
    HARASSMENT = "harassment"
    SPAM = "spam"
    OTHER = "other"


class ReportStatus(str, enum.Enum):
    """Lifecycle of a moderation report."""

    OPEN = "open"
    UNDER_REVIEW = "under_review"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


class ChatAuditAction(str, enum.Enum):
    """Actions recorded in chat_audit_log. See design §4.1."""

    MESSAGE_SENT = "message_sent"
    MESSAGE_EDITED = "message_edited"
    MESSAGE_DELETED = "message_deleted"
    CHANNEL_JOINED = "channel_joined"
    CHANNEL_LEFT = "channel_left"
    MEMBER_ADDED = "member_added"
    MEMBER_REMOVED = "member_removed"
    ROLE_CHANGED = "role_changed"
    REPORT_FILED = "report_filed"
    REPORT_RESOLVED = "report_resolved"
    SAFEGUARDING_ACTION = "safeguarding_action"
    CHANNEL_ARCHIVED = "channel_archived"
