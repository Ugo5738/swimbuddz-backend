import enum


class ClubBillingCycle(str, enum.Enum):
    QUARTERLY = "quarterly"
    BIANNUAL = "biannual"
    ANNUAL = "annual"


class SessionAttendanceStatus(str, enum.Enum):
    PRESENT = "present"
    ABSENT = "absent"
    LATE = "late"
    EXCUSED = "excused"
    CANCELLED = "cancelled"


class SessionAttendanceRole(str, enum.Enum):
    SWIMMER = "swimmer"
