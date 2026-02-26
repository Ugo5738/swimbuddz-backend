"""Sessions service routers."""

from services.sessions_service.routers.internal import router as internal_router
from services.sessions_service.routers.member import router as sessions_router
from services.sessions_service.routers.templates import router as templates_router

__all__ = [
    "internal_router",
    "sessions_router",
    "templates_router",
]
