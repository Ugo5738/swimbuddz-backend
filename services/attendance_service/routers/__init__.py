"""Attendance service routers."""

from services.attendance_service.routers.internal import router as internal_router
from services.attendance_service.routers.member import router as attendance_router

__all__ = [
    "attendance_router",
    "internal_router",
]
