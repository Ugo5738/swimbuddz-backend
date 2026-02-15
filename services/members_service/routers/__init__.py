"""Members service routers package."""

from services.members_service.routers.admin import router as admin_router
from services.members_service.routers.coaches import router as coaches_router
from services.members_service.routers.internal import router as internal_router
from services.members_service.routers.members import router as members_router
from services.members_service.routers.registration import router as registration_router

__all__ = [
    "registration_router",
    "members_router",
    "coaches_router",
    "admin_router",
    "internal_router",
]
