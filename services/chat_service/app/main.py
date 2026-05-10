"""FastAPI application for the Chat Service.

Design doc: docs/design/CHAT_SERVICE_DESIGN.md
"""

from fastapi import FastAPI

from services.chat_service.routers.admin import router as admin_router
from services.chat_service.routers.internal import router as internal_router
from services.chat_service.routers.member import router as member_router


def create_app() -> FastAPI:
    """Create and configure the Chat Service FastAPI app."""
    app = FastAPI(
        title="SwimBuddz Chat Service",
        version="0.1.0",
        description=(
            "Real-time, persistent, role-aware messaging across SwimBuddz. "
            "See docs/design/CHAT_SERVICE_DESIGN.md."
        ),
    )

    @app.get("/health", tags=["system"])
    async def health_check() -> dict[str, str]:
        """Health check endpoint."""
        return {"status": "ok", "service": "chat"}

    # Member-facing routes: gateway proxies /api/v1/chat/* → /chat/*
    app.include_router(member_router)
    # Admin / moderator routes: gateway proxies /api/v1/admin/chat/* → /admin/chat/*
    app.include_router(admin_router)
    # Internal service-to-service routes (not proxied by gateway)
    app.include_router(internal_router)

    return app


app = create_app()
