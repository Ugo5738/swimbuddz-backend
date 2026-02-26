"""Transport service routers package."""

from services.transport_service.routers.areas import router as areas_router
from services.transport_service.routers.bookings import router as bookings_router
from services.transport_service.routers.routes import router as routes_router

__all__ = ["areas_router", "bookings_router", "routes_router"]
