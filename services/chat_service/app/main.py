"""FastAPI application for the Chat Service.

Phase 0 scaffolding — service boots with a health endpoint only.
Real chat endpoints, models, and real-time transport are added in Phase 1.

Design doc: docs/design/CHAT_SERVICE_DESIGN.md
"""

from fastapi import FastAPI


def create_app() -> FastAPI:
    """Create and configure the Chat Service FastAPI app."""
    app = FastAPI(
        title="SwimBuddz Chat Service",
        version="0.1.0",
        description=(
            "Real-time, persistent, role-aware messaging across SwimBuddz. "
            "Phase 0 — scaffolding only. See docs/design/CHAT_SERVICE_DESIGN.md."
        ),
    )

    @app.get("/health", tags=["system"])
    async def health_check() -> dict[str, str]:
        """Health check endpoint."""
        return {"status": "ok", "service": "chat"}

    # Phase 1 routers are registered here as they are built:
    # - Member-facing:  /chat/*                (gateway: /api/v1/chat/*)
    # - Admin/mod:      /admin/chat/*          (gateway: /api/v1/admin/chat/*)
    # - Internal s2s:   /internal/chat/*       (not proxied by gateway)

    return app


app = create_app()
