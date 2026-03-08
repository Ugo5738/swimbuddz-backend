"""Pools service routers package."""

from services.pools_service.routers.admin import router as admin_router
from services.pools_service.routers.public import router as public_router

__all__ = [
    "admin_router",
    "public_router",
]
