"""FastAPI application for the AI Service."""

from fastapi import FastAPI

from libs.common.health import register_health_check
from services.ai_service.routers.admin_analyze import (
    admin_router as strokelab_admin_router,
)
from services.ai_service.routers.analyze import router as analyze_router
from services.ai_service.routers.founding_members import (
    internal_router as founding_internal_router,
)
from services.ai_service.routers.founding_members import (
    router as founding_members_router,
)
from services.ai_service.routers.member import admin_router, router
from services.ai_service.routers.public import router as public_router


def create_app() -> FastAPI:
    """Create and configure the AI Service FastAPI app."""
    app = FastAPI(
        title="SwimBuddz AI Service",
        version="0.1.0",
        description="AI-assisted scoring and intelligence service for SwimBuddz.",
    )

    register_health_check(app, "ai")

    # Include scoring + service endpoints
    app.include_router(router, prefix="/ai")

    # Include admin endpoints
    app.include_router(admin_router, prefix="/ai")

    # Stroke Lab — swim-video analysis endpoints
    app.include_router(analyze_router, prefix="/ai")
    app.include_router(strokelab_admin_router, prefix="/ai")
    # Stroke Lab — PUBLIC guest analyzer (no auth; /ai/public/*)
    app.include_router(public_router, prefix="/ai")
    app.include_router(founding_members_router, prefix="/ai")
    # Internal router is mounted at root so payments_service can reach
    # /internal/ai/founding-members/confirm (no /ai prefix).
    app.include_router(founding_internal_router)

    return app


app = create_app()
