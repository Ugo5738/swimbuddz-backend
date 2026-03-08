"""FastAPI application for the Pools Service."""

from fastapi import FastAPI

from services.pools_service.routers import admin_router, public_router


def create_app() -> FastAPI:
    """Create and configure the Pools Service FastAPI app."""
    app = FastAPI(
        title="SwimBuddz Pools Service",
        version="0.1.0",
        description="Pool registry and partnership CRM for SwimBuddz.",
    )

    @app.get("/health", tags=["system"])
    async def health_check() -> dict[str, str]:
        """Health check endpoint."""
        return {"status": "ok", "service": "pools"}

    # Public routes (active partner pools only)
    app.include_router(public_router, prefix="/pools")

    # Admin routes (full CRUD, all pools)
    app.include_router(admin_router, prefix="/admin/pools")

    return app


app = create_app()
