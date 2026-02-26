"""Volunteer service routers."""

from services.volunteer_service.routers.admin import router as admin_router
from services.volunteer_service.routers.member import router as volunteer_router

__all__ = [
    "admin_router",
    "volunteer_router",
]
