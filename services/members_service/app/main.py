"""FastAPI application for the Members Service."""

from fastapi import FastAPI

from services.members_service.router import (
    router as members_router,
    pending_router,
    admin_router,
)
from services.members_service.volunteer_router import volunteer_router, challenge_router


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
    app.include_router(members_router)
    app.include_router(pending_router)  # Fix: Include pending registration router
    app.include_router(admin_router)  # Admin approval endpoints
    app.include_router(volunteer_router)
    app.include_router(challenge_router)

    return app


app = create_app()

