"""FastAPI application for the Pools Service."""

from fastapi import FastAPI

from services.pools_service.routers import (
    admin_related_router,
    admin_router,
    admin_submissions_router,
    public_router,
    submissions_router,
)


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

    return app


app = create_app()
