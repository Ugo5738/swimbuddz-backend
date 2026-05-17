"""Attendance Service models package."""

from services.attendance_service.models.booking import SessionBooking
from services.attendance_service.models.core import (
    AttendanceRecord,
    AttendanceRole,
    AttendanceStatus,
    MemberRef,
)
from services.attendance_service.models.enums import (
    BookingChannel,
    SessionBookingStatus,
)

__all__ = [
    "AttendanceRecord",
    "AttendanceRole",
    "AttendanceStatus",
    "BookingChannel",
    "MemberRef",
    "SessionBooking",
    "SessionBookingStatus",
]
