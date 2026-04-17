"""Pools service routers package."""

from services.pools_service.routers.admin import router as admin_router
from services.pools_service.routers.admin_related import (
    router as admin_related_router,
)
from services.pools_service.routers.admin_submissions import (
    router as admin_submissions_router,
)
from services.pools_service.routers.public import router as public_router
from services.pools_service.routers.submissions import router as submissions_router

__all__ = [
    "admin_related_router",
    "admin_router",
    "admin_submissions_router",
    "public_router",
    "submissions_router",
]
