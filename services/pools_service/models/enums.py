"""Enum definitions for pools service models."""

import enum


def enum_values(enum_cls):
    """Return persistent DB values for SAEnum mappings."""
    return [member.value for member in enum_cls]


class PartnershipStatus(str, enum.Enum):
    PROSPECT = "prospect"
    EVALUATING = "evaluating"
    ACTIVE_PARTNER = "active_partner"
    INACTIVE = "inactive"
    REJECTED = "rejected"


class PoolType(str, enum.Enum):
    COMMUNITY = "community"
    CLUB = "club"
    ACADEMY = "academy"
    PRIVATE = "private"
    PUBLIC = "public"
    HOTEL = "hotel"


class IndoorOutdoor(str, enum.Enum):
    INDOOR = "indoor"
    OUTDOOR = "outdoor"
    BOTH = "both"


class PreferredContactChannel(str, enum.Enum):
    WHATSAPP = "whatsapp"
    PHONE = "phone"
    EMAIL = "email"
    IN_PERSON = "in_person"


class PoolSource(str, enum.Enum):
    MEMBER_SUBMISSION = "member_submission"
    TEAM_SCOUTING = "team_scouting"
    REFERRAL = "referral"
    DIRECT_OUTREACH = "direct_outreach"
    OTHER = "other"


class PoolContactRole(str, enum.Enum):
    OWNER = "owner"
    MANAGER = "manager"
    FRONT_DESK = "front_desk"
    ACCOUNTANT = "accountant"
    OPERATIONS = "operations"
    MARKETING = "marketing"
    OTHER = "other"


class PoolVisitType(str, enum.Enum):
    SCOUTING = "scouting"
    EVALUATION = "evaluation"
    PARTNERSHIP_MEETING = "partnership_meeting"
    SESSION_CHECK = "session_check"
    INCIDENT = "incident"
    OTHER = "other"


class PoolAgreementStatus(str, enum.Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    EXPIRED = "expired"
    TERMINATED = "terminated"


class PoolAssetType(str, enum.Enum):
    PHOTO = "photo"
    DOCUMENT = "document"
    VIDEO = "video"
    CERTIFICATE = "certificate"
    OTHER = "other"
