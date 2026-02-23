"""Enum definitions for transport service models."""

import enum


class RideShareOption(str, enum.Enum):
    NONE = "none"
    LEAD = "lead"
    JOIN = "join"
