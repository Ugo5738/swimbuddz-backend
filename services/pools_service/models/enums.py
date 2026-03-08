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
