"""Communications service routers package."""

from services.communications_service.routers.announcement_categories import (
    category_router,
)
from services.communications_service.routers.announcements import admin_router, router
from services.communications_service.routers.content import content_router

__all__ = [
    "admin_router",
    "category_router",
    "content_router",
    "router",
]
