"""FastAPI application for the Members Service."""

from fastapi import FastAPI
from services.members_service.coach_router import admin_router as coach_admin_router
from services.members_service.coach_router import router as coach_router
from services.members_service.router import (
    admin_router,
    coaches_router,
    registration_router,
)
from services.members_service.router import router as members_router
from services.members_service.routers.internal import router as internal_router
from services.members_service.volunteer_router import challenge_router


def create_app() -> FastAPI:
    """Create and configure the Members Service FastAPI app."""
    app = FastAPI(
        title="SwimBuddz Members Service",
        version="0.1.0",
        description="Member management service for SwimBuddz.",
    )

    @app.get("/health", tags=["system"])
    async def health_check() -> dict[str, str]:
        """Health check endpoint."""
        return {"status": "ok", "service": "members"}

    # Include routers
    app.include_router(coaches_router)  # Public coaches listing endpoints
    app.include_router(members_router)
    app.include_router(registration_router)  # Registration flow endpoints
    app.include_router(admin_router)  # Admin approval endpoints
    # NOTE: volunteer_router removed — now handled by volunteer_service (port 8012)
    app.include_router(challenge_router)

    # Coach routers (profile management, not public listing)
    app.include_router(coach_router)
    app.include_router(coach_admin_router)

    # Internal service-to-service endpoints (not exposed via gateway)
    app.include_router(internal_router)

    return app


app = create_app()
