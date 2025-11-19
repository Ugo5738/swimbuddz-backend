"""FastAPI application entrypoint for the SwimBuddz gateway service."""
from __future__ import annotations

from fastapi import FastAPI
from dotenv import load_dotenv

load_dotenv()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application instance."""
    app = FastAPI(
        title="SwimBuddz Gateway Service",
        version="0.1.0",
        description="Backend-for-frontend that orchestrates SwimBuddz domain services.",
    )

    @app.get("/health", tags=["system"])
    async def health_check() -> dict[str, str]:  # pragma: no cover - trivial wiring
        """Simple readiness endpoint used during bootstrap."""
        return {"status": "ok"}

    from services.members_service.router import router as members_router
    from services.members_service.router import pending_router
    from services.sessions_service.router import router as sessions_router
    from services.attendance_service.router import router as attendance_router
    from services.communications_service.router import router as communications_router
    
    app.include_router(members_router, prefix="/api/v1")
    app.include_router(pending_router, prefix="/api/v1")
    app.include_router(sessions_router, prefix="/api/v1")
    from services.communications_service.router import router as communications_router
    from services.payments_service.router import router as payments_router
    from services.academy_service.router import router as academy_router
    
    app.include_router(members_router, prefix="/api/v1")
    app.include_router(pending_router, prefix="/api/v1")
    app.include_router(sessions_router, prefix="/api/v1")
    app.include_router(attendance_router, prefix="/api/v1")
    app.include_router(communications_router, prefix="/api/v1")
    app.include_router(payments_router, prefix="/api/v1")
    app.include_router(academy_router, prefix="/api/v1")
    
    from services.gateway_service.app.routers.dashboard import router as dashboard_router
    app.include_router(dashboard_router, prefix="/api/v1")

    return app


app = create_app()
