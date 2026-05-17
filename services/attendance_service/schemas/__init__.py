"""Attendance Service schemas package."""

from services.attendance_service.schemas.enums import RideShareOption
from services.attendance_service.schemas.main import (
    AdminBookingCreate,
    AttendanceBase,
    AttendanceCreate,
    AttendanceResponse,
    BulkBookingItem,
    BulkBookingRequest,
    BulkBookingResponse,
    CoachAttendanceMarkEntry,
    CoachAttendanceMarkRequest,
    CoachAttendanceMarkResponse,
    CohortAttendanceSummary,
    PublicAttendanceCreate,
    SessionBookingCreate,
    SessionBookingResponse,
    StudentAttendanceSummary,
)

__all__ = [
    "AdminBookingCreate",
    "AttendanceBase",
    "AttendanceCreate",
    "AttendanceResponse",
    "BulkBookingItem",
    "BulkBookingRequest",
    "BulkBookingResponse",
    "CoachAttendanceMarkEntry",
    "CoachAttendanceMarkRequest",
    "CoachAttendanceMarkResponse",
    "CohortAttendanceSummary",
    "PublicAttendanceCreate",
    "RideShareOption",
    "SessionBookingCreate",
    "SessionBookingResponse",
    "StudentAttendanceSummary",
]
