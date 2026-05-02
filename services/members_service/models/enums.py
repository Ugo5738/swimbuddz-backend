"""Enum definitions for members service models."""

import enum


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
