"""Enum definitions for attendance service models."""

import enum


def enum_values(enum_cls):
    """Return persistent DB values for SAEnum mappings."""
    return [member.value for member in enum_cls]


class AttendanceStatus(str, enum.Enum):
    PRESENT = "present"
    ABSENT = "absent"
    LATE = "late"
    EXCUSED = "excused"
    CANCELLED = "cancelled"


class AttendanceRole(str, enum.Enum):
    SWIMMER = "swimmer"
    COACH = "coach"
    VOLUNTEER = "volunteer"
    GUEST = "guest"
