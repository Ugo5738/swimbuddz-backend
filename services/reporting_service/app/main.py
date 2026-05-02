"""FastAPI application for the Reporting Service."""

from fastapi import FastAPI

from services.reporting_service.routers.admin_flywheel import (
    router as admin_flywheel_router,
)
from services.reporting_service.routers.admin_reports import (
    router as admin_reports_router,
)
from services.reporting_service.routers.community_reports import (
    router as community_reports_router,
)
from services.reporting_service.routers.internal import router as internal_router
from services.reporting_service.routers.member_reports import (
    router as member_reports_router,
)
from services.reporting_service.routers.seasonality import router as seasonality_router


def create_app() -> FastAPI:
    """Create and configure the Reporting Service FastAPI app."""
    app = FastAPI(
        title="SwimBuddz Reporting Service",
        version="0.1.0",
        description="Quarterly reporting, analytics, and shareable card generation.",
    )

    @app.get("/health", tags=["system"])
    async def health_check() -> dict[str, str]:
        """Health check endpoint."""
        return {"status": "ok", "service": "reporting"}

    # Member-facing report routes
    # Gateway: /api/v1/reports/{path} → /reports/{path}
    app.include_router(member_reports_router)

    # Community report routes
    # Gateway: /api/v1/reports/community/{path} → /reports/community/{path}
    app.include_router(community_reports_router)

    # Admin report routes
    # Gateway: /api/v1/admin/reports/{path} → /admin/reports/{path}
    app.include_router(admin_reports_router)

    # Admin seasonality forecast routes
    # Gateway: /api/v1/admin/reports/seasonality/{path} → /admin/reports/seasonality/{path}
    app.include_router(seasonality_router)

    # Admin flywheel metrics routes
    # Gateway: /api/v1/admin/reports/flywheel/{path} → /admin/reports/flywheel/{path}
    app.include_router(admin_flywheel_router)

    # Internal service-to-service routes (not proxied by gateway)
    app.include_router(internal_router)

    return app


app = create_app()
