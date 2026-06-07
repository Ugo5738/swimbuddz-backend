"""FastAPI application for the Pools Service."""

from fastapi import FastAPI

from libs.common.health import register_health_check
from services.pools_service.routers import (
    admin_related_router,
    admin_router,
    admin_submissions_router,
    public_router,
    submissions_router,
)
from services.pools_service.weather.routers import (
    admin_router as weather_admin_router,
    member_router as weather_member_router,
)


def create_app() -> FastAPI:
    """Create and configure the Pools Service FastAPI app."""
    app = FastAPI(
        title="SwimBuddz Pools Service",
        version="0.1.0",
        description="Pool registry and partnership CRM for SwimBuddz.",
    )

    register_health_check(app, "pools")

    # Member-facing submission routes (auth required)
    # Registered before admin routes so the gateway can proxy /pools/submissions
    # without colliding with /pools/{pool_id}.
    app.include_router(submissions_router, prefix="/pools/submissions")

    # Public routes (active partner pools only)
    app.include_router(public_router, prefix="/pools")

    # Admin submissions (must be registered before /admin/pools/{pool_id})
    app.include_router(admin_submissions_router, prefix="/admin/pools/submissions")

    # Admin CRUD for pool-related entities (contacts, visits, agreements, assets, status history)
    # Mounted under /admin/pools so routes become /admin/pools/{pool_id}/contacts etc.
    app.include_router(admin_related_router, prefix="/admin/pools")

    # Admin routes (full CRUD, all pools)
    app.include_router(admin_router, prefix="/admin/pools")

    # Weather module — cached forecasts for pool locations. Hosted here because
    # pools owns the coordinates the forecast keys on (no cross-service hop).
    app.include_router(weather_member_router, prefix="/weather")
    app.include_router(weather_admin_router, prefix="/admin/weather")

    return app


app = create_app()
