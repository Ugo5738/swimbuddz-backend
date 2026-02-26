"""AI service routers."""

from services.ai_service.routers.member import admin_router, router

__all__ = [
    "admin_router",
    "router",
]
