"""Events service routers."""

from services.events_service.routers.member import router as events_router

__all__ = [
    "events_router",
]
