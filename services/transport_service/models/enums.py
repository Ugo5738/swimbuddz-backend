"""Enum definitions for transport service models."""

import enum


class RideShareOption(str, enum.Enum):
    NONE = "none"
    LEAD = "lead"
    JOIN = "join"


class RidePassengerType(str, enum.Enum):
    MEMBER = "member"
    SESSION_GUEST = "session_guest"
    OBSERVER = "observer"
