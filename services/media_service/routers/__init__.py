"""Media service routers package."""

from services.media_service.routers.albums import router as albums_router
from services.media_service.routers.assets import router as assets_router
from services.media_service.routers.media import router as media_router

__all__ = [
    "albums_router",
    "assets_router",
    "media_router",
]
