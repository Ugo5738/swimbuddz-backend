"""FastAPI application for the AI Service."""

from fastapi import FastAPI
from services.ai_service.routers.member import admin_router, router


def create_app() -> FastAPI:
    """Create and configure the AI Service FastAPI app."""
    app = FastAPI(
        title="SwimBuddz AI Service",
        version="0.1.0",
        description="AI-assisted scoring and intelligence service for SwimBuddz.",
    )

    @app.get("/health", tags=["system"])
    async def health_check() -> dict[str, str]:
        """Health check endpoint."""
        return {"status": "ok", "service": "ai"}

    # Include scoring + service endpoints
    app.include_router(router, prefix="/ai")

    # Include admin endpoints
    app.include_router(admin_router, prefix="/ai")

    return app


app = create_app()
